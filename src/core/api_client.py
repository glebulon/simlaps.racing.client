"""
API Client for SimLaps server communication.

Handles lap time submissions with signed payloads for anti-cheat.
No API key required - uses embedded app secret for signing.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

import httpx

from ..models import (
    LapData,
    SessionData,
    SharedSessionManager,
    SubmissionSnapshot,
)
from ..utils.structured_logger import (
    Component,
    log_debug,
    log_error,
    log_exception,
    log_info,
    log_warning,
)
from ..version import USER_AGENT, VERSION
from .security import (
    GameProcessStatus,
    get_app_secret,
    get_secret_source,
    is_game_running,
    is_secret_configured,
    sign_payload,
    verify_signature_locally,
)


class SubmissionStatus(Enum):
    """Status of a lap submission."""

    SUCCESS = "success"
    ERROR = "error"
    INVALID_LAP = "invalid_lap"
    SIGNATURE_ERROR = "signature_error"
    REPLAY_REJECTED = "replay_rejected"
    RATE_LIMITED = "rate_limited"
    GAME_NOT_RUNNING = "game_not_running"
    NETWORK_ERROR = "network_error"
    PLAUSIBILITY_FAILED = "plausibility_failed"
    NO_SECRET = "no_secret"  # noqa: S105


@dataclass
class SubmissionResult:
    """Result of a lap submission attempt."""

    status: SubmissionStatus
    message: str
    lap_id: Optional[str] = None


class APIClient:
    """
    Client for communicating with the SimLaps API.

    Uses signed payloads instead of API keys for authentication.
    All submissions are cryptographically signed with an embedded app secret.
    """

    DEFAULT_SERVER_URL = "https://simlaps.racing"
    SUBMIT_ENDPOINT = "/api/submit"
    TIMEOUT = 30.0

    def __init__(
        self,
        server_url: Optional[str] = None,
        session_manager: Optional[SharedSessionManager] = None,
    ):
        """
        Initialize API client.

        Args:
            server_url: Base URL of the SimLaps server
        """
        self.server_url = (server_url or self.DEFAULT_SERVER_URL).rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._session_manager = session_manager or SharedSessionManager()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.TIMEOUT,
                headers=self._get_headers(),
                follow_redirects=True,
            )
        return self._client

    def _get_headers(self) -> dict:
        """Get request headers."""
        return {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Client-Version": VERSION,
        }

    def set_server_url(self, server_url: str) -> None:
        """
        Set the server URL.

        Args:
            server_url: Base URL of the SimLaps server
        """
        self.server_url = server_url.rstrip("/")

    async def submit_lap(
        self,
        session: SessionData,
        lap: LapData,
        user_id: Optional[str] = None,
        submit_invalid: bool = False,
    ) -> SubmissionResult:
        """Submit a completed lap to the server."""
        log_debug(
            Component.API,
            "submit_lap called",
            lap_time=lap.lap_time_str,
            lap_time_ms=lap.lap_time_ms,
            is_valid=lap.is_valid,
            submit_invalid=submit_invalid,
        )

        # Offline mode must remain first: no game detection, signing, or HTTP.
        if not is_secret_configured():
            log_info(
                Component.API,
                "Submission skipped: APP_SECRET not configured (offline mode)",
            )
            return SubmissionResult(
                status=SubmissionStatus.NO_SECRET,
                message="APP_SECRET not configured — running in offline mode",
            )

        # The parser's completed-lap verdict is authoritative. Graphics SHM
        # cannot distinguish contact from track cuts and must not override it.
        effective_is_valid = lap.is_valid
        log_debug(
            Component.API,
            "Effective validity",
            effective_is_valid=effective_is_valid,
        )

        # Fail closed when process detection is unavailable or ACE is stopped.
        game_status = is_game_running()
        log_debug(Component.API, "Game running check", game_status=game_status)
        if game_status != GameProcessStatus.RUNNING:
            log_warning(
                Component.API,
                "Rejected: Game not running or detection uncertain",
            )
            return SubmissionResult(
                status=SubmissionStatus.GAME_NOT_RUNNING,
                message="Game must be running to submit laps",
            )

        final_user_id, shared_snapshot, preflight_error = self._validate_submission_preflight(
            session=session,
            effective_is_valid=effective_is_valid,
            user_id=user_id,
            submit_invalid=submit_invalid,
            lap_number=lap.lap_number,
        )
        if preflight_error is not None:
            return preflight_error
        if final_user_id is None:
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message="No Steam ID detected - please start a session in game",
            )

        payload, payload_error = self._build_submission_payload(
            session=session,
            lap=lap,
            final_user_id=final_user_id,
            shared_snapshot=shared_snapshot,
            effective_is_valid=effective_is_valid,
        )
        if payload_error is not None:
            return payload_error
        if payload is None:
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message="Unable to build lap submission payload",
            )

        signed_payload = sign_payload(payload)
        local_sig_valid = verify_signature_locally(signed_payload)
        log_debug(
            Component.API,
            "Signature computed",
            local_verifies=local_sig_valid,
            secret_source=get_secret_source(),
            secret_len=len(get_app_secret()),
            sig_data=(
                f"{signed_payload['_timestamp']}:{signed_payload['_nonce']}:"
                f"{payload.get('userId')}:{payload.get('trackId')}:{payload.get('time')}"
            ),
            signature_prefix=signed_payload["_signature"][:12],
        )
        log_debug(
            Component.API,
            "Sending submission",
            server_url=f"{self.server_url}{self.SUBMIT_ENDPOINT}",
            payload_keys=list(signed_payload.keys()),
        )

        transport_result = await self._send_submission(signed_payload)
        if isinstance(transport_result, SubmissionResult):
            return transport_result
        return self._map_submission_response(transport_result, signed_payload)

    def _validate_submission_preflight(
        self,
        *,
        session: SessionData,
        effective_is_valid: bool,
        user_id: Optional[str],
        submit_invalid: bool,
        lap_number: Optional[int] = None,
    ) -> tuple[
        Optional[str], Optional[SubmissionSnapshot], Optional[SubmissionResult]
    ]:
        """Validate submission policy and resolve the authoritative user."""
        if not effective_is_valid and not submit_invalid:
            log_debug(
                Component.API,
                "Rejected: Invalid lap and submit_invalid=False",
            )
            return (
                None,
                None,
                SubmissionResult(
                status=SubmissionStatus.INVALID_LAP,
                message="Lap was invalidated (penalty or off-track)",
                ),
            )

        get_origin = getattr(self._session_manager, "get_session_origin", None)
        get_snapshot = getattr(self._session_manager, "get_submission_snapshot_for_origin", None)
        shared_snapshot: Optional[SubmissionSnapshot] = None
        shared_player = None
        relation = None
        initial_state = False
        if not callable(get_origin) or not callable(get_snapshot):
            # Legacy test doubles and older integrations have no provenance
            # API. Preserve their original getter precedence without inventing
            # a modern overlay.
            get_player = getattr(self._session_manager, "get_player_identification", None)
            shared_player = get_player() if callable(get_player) else None
            final_user_id = user_id or session.player_id or getattr(shared_player, "steam_id", None)
        else:
            origin = get_origin()
            initial_state = origin.session_id is None and origin.epoch == 0
            result = get_snapshot(origin, lap_number=lap_number, session_id=session.session_id)
            relation = getattr(result, "relation", None)
            shared_snapshot = getattr(result, "snapshot", None)
            if not isinstance(shared_snapshot, SubmissionSnapshot):
                shared_snapshot = None
            shared_player = shared_snapshot
            final_user_id = user_id or session.player_id or getattr(shared_player, "steam_id", None)
        log_debug(
            Component.API,
            "User ID resolution",
            user_id=user_id,
            session_player_id=session.player_id,
            shared_steam_id=getattr(shared_player, "steam_id", None),
            shared_belongs_to_session=shared_snapshot is not None,
            final_user_id=final_user_id,
        )
        if not final_user_id:
            log_warning(Component.API, "Rejected: No user ID")
            return None, shared_snapshot, SubmissionResult(
                status=SubmissionStatus.ERROR,
                message="No Steam ID detected - please start a session in game",
            )
        relation_value = getattr(relation, "value", relation)
        accepted_snapshot = relation_value in {"active", "late_bound", "closed"}
        # At process bootstrap there is no established session owner. Keep the
        # historical identity-only fallback while avoiding an unrelated
        # metadata/timing overlay from the manager's initial placeholder state.
        if initial_state:
            shared_snapshot = None
        return final_user_id, shared_snapshot if accepted_snapshot else None, None

    def _build_submission_payload(
        self,
        *,
        session: SessionData,
        lap: LapData,
        final_user_id: str,
        shared_snapshot: Optional[SubmissionSnapshot],
        effective_is_valid: bool,
    ) -> tuple[Optional[Dict[str, Any]], Optional[SubmissionResult]]:
        """Build the source-aware unsigned payload without changing precedence."""
        legacy_timing = None
        legacy_sectors = None
        legacy_fuel = None
        legacy_metadata = None
        legacy_player = None
        snapshot_track = snapshot_car = snapshot_session_type = None
        snapshot_game_version = snapshot_session_id = None
        snapshot_lap_time = snapshot_sectors = snapshot_fuel = None
        modern_manager = callable(
            getattr(self._session_manager, "get_session_origin", None)
        ) and callable(
            getattr(self._session_manager, "get_submission_snapshot_for_origin", None)
        )
        if isinstance(shared_snapshot, SubmissionSnapshot):
            snapshot_track = shared_snapshot.track
            snapshot_car = shared_snapshot.car_model
            snapshot_session_type = shared_snapshot.session_type
            snapshot_game_version = shared_snapshot.game_version
            snapshot_session_id = shared_snapshot.retained_session_id
            snapshot_lap_time = shared_snapshot.lap_time_ms
            snapshot_sectors = shared_snapshot.sectors
            snapshot_fuel = shared_snapshot.fuel_per_lap
        elif not modern_manager:
            snapshot_track = snapshot_car = snapshot_session_type = snapshot_game_version = None
            snapshot_session_id = snapshot_lap_time = snapshot_sectors = snapshot_fuel = None
            get_timing = getattr(self._session_manager, "get_lap_timing_data", None)
            get_sectors = getattr(self._session_manager, "get_sector_split_data", None)
            get_fuel = getattr(self._session_manager, "get_fuel_data", None)
            get_metadata = getattr(self._session_manager, "get_session_metadata_data", None)
            get_player = getattr(self._session_manager, "get_player_identification", None)
            legacy_timing = get_timing(lap.lap_number) if callable(get_timing) else None
            legacy_sectors = get_sectors(lap.lap_number) if callable(get_sectors) else None
            legacy_fuel = get_fuel() if callable(get_fuel) else None
            legacy_metadata = get_metadata() if callable(get_metadata) else None
            legacy_player = get_player() if callable(get_player) else None
        shared_lap_time = (
            snapshot_lap_time
            if isinstance(shared_snapshot, SubmissionSnapshot)
            else getattr(legacy_timing, "last_lap_time_ms", None)
        )
        shared_sector_values = snapshot_sectors if isinstance(shared_snapshot, SubmissionSnapshot) else (
            getattr(legacy_sectors, "sector1_ms", None),
            getattr(legacy_sectors, "sector2_ms", None),
            getattr(legacy_sectors, "sector3_ms", None),
        )
        shared_fuel_value = (
            snapshot_fuel
            if isinstance(shared_snapshot, SubmissionSnapshot)
            else getattr(legacy_fuel, "fuel_consumed_lap", None)
        )

        effective_track = (
            session.track
            if session.track and session.track != "Unknown"
            else (snapshot_track or getattr(legacy_metadata, "track", "Unknown"))
        )
        effective_car = (
            session.car
            if session.car and session.car != "Unknown"
            else (
                snapshot_car or getattr(legacy_player, "car_model", None) or session.car
            )
        )
        effective_session_id = session.session_id or (
            snapshot_session_id or getattr(legacy_metadata, "session_id", None)
        )
        effective_session_type = (
            session.session_type
            if session.session_type and session.session_type != "Unknown"
            else (
                snapshot_session_type or getattr(legacy_metadata, "session_type", session.session_type)
            )
        )
        effective_game_version = (
            session.game_version
            if session.game_version and session.game_version != "Unknown"
            else (
                snapshot_game_version or getattr(legacy_metadata, "game_version", session.game_version)
            )
        )

        track_id = self._normalize_track_id(effective_track)
        log_debug(
            Component.API,
            "Track normalization",
            effective_track=effective_track,
            track_id=track_id,
            car=effective_car,
            lap_time_ms=lap.lap_time_ms,
            shared_lap_time_ms=shared_lap_time,
        )

        # The parser's lap time is authoritative. SHM is only a missing-time
        # fallback because its session-global value can still describe lap N-1.
        final_time_candidate: Any = lap.lap_time_ms
        if (
            not isinstance(final_time_candidate, (int, float)) or int(final_time_candidate) <= 0
        ) and shared_lap_time is not None:
            final_time_candidate = shared_lap_time

        if not isinstance(final_time_candidate, (int, float)):
            log_debug(Component.API, "Rejected: Lap time unavailable")
            return None, SubmissionResult(
                status=SubmissionStatus.INVALID_LAP,
                message="Invalid lap time (missing or non-numeric)",
            )

        final_time = int(final_time_candidate)
        if final_time <= 0:
            log_debug(
                Component.API,
                "Rejected: Invalid lap time",
                final_time=final_time,
            )
            return None, SubmissionResult(
                status=SubmissionStatus.INVALID_LAP,
                message="Invalid lap time (<= 0)",
            )

        payload: Dict[str, Any] = {
            "userId": final_user_id,
            "trackId": track_id,
            "carId": effective_car,
            "time": final_time,
            "sessionId": effective_session_id,
            "sessionType": effective_session_type,
            "gameVersion": effective_game_version,
            "tires": lap.tyre_compound,
            "valid": effective_is_valid,
        }

        sector_payload: Dict[str, Any] = {
            "sector1": lap.sector1_ms,
            "sector2": lap.sector2_ms,
            "sector3": lap.sector3_ms,
        }
        if shared_sector_values is not None:
            for field_name in ("sector1", "sector2", "sector3"):
                value = sector_payload[field_name]
                if not isinstance(value, (int, float)) or int(value) <= 0:
                    sector_payload[field_name] = shared_sector_values[
                        ("sector1", "sector2", "sector3").index(field_name)
                    ]

        for field_name, value in sector_payload.items():
            if value is not None and int(value) > 0:
                payload[field_name] = int(value)

        # SHM's per-lap fuel is authoritative; parsed log fuel is the fallback.
        fuel_used_value = shared_fuel_value
        if fuel_used_value is None:
            fuel_used_value = lap.fuel_used
        if fuel_used_value is not None:
            try:
                fuel_value = float(fuel_used_value)
                if fuel_value >= 0:
                    payload["fuelUsed"] = fuel_value
            except (ValueError, TypeError):
                pass

        if session.setup_notes:
            setup_notes = session.setup_notes.strip()
            if setup_notes:
                payload["setupNotes"] = setup_notes

        return payload, None

    async def _send_submission(
        self,
        signed_payload: Dict[str, Any],
    ) -> httpx.Response | SubmissionResult:
        """Perform HTTP transport and map transport-level exceptions."""
        try:
            client = await self._get_client()
            return await client.post(
                f"{self.server_url}{self.SUBMIT_ENDPOINT}",
                json=signed_payload,
            )
        except httpx.NetworkError as exc:
            detail = str(exc).strip() or type(exc).__name__
            log_error(Component.API, "Network error", error=detail)
            return SubmissionResult(
                status=SubmissionStatus.NETWORK_ERROR,
                message=f"Network error: {detail}",
            )
        except httpx.TimeoutException:
            log_error(Component.API, "Request timeout")
            return SubmissionResult(
                status=SubmissionStatus.NETWORK_ERROR,
                message="Request timed out",
            )
        except (RuntimeError, OSError, ConnectionError) as exc:
            log_exception(Component.API, "API exception", exc)
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message=f"Unexpected error: {str(exc)}",
            )

    def _map_submission_response(
        self,
        response: httpx.Response,
        signed_payload: Dict[str, Any],
    ) -> SubmissionResult:
        """Map an HTTP response to the stable public result contract."""
        log_debug(
            Component.API,
            "Response status",
            status_code=response.status_code,
        )

        if response.status_code == 201:
            data = response.json()
            log_info(Component.API, "SUCCESS", lap_id=data.get("id"))
            return SubmissionResult(
                status=SubmissionStatus.SUCCESS,
                message="Lap submitted successfully",
                lap_id=data.get("id"),
            )
        if response.status_code == 401:
            try:
                error_data = response.json() if response.content else {}
            except (ValueError, KeyError, TypeError):
                error_data = {}
            log_warning(
                Component.API,
                "401 signature error",
                server_code=(error_data.get("code") if isinstance(error_data, dict) else None),
                server_error=(error_data.get("error") if isinstance(error_data, dict) else None),
                error_data=error_data,
            )
            return SubmissionResult(
                status=SubmissionStatus.SIGNATURE_ERROR,
                message="Signature verification failed - please update the app",
            )
        if response.status_code == 409:
            error_data = response.json()
            error_msg = error_data.get("error", "Conflict")
            if "nonce" in error_msg.lower() or "replay" in error_msg.lower():
                return SubmissionResult(
                    status=SubmissionStatus.REPLAY_REJECTED,
                    message="Replay attack detected - submission rejected",
                )
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message="Duplicate lap already exists",
            )
        if response.status_code == 429:
            return SubmissionResult(
                status=SubmissionStatus.RATE_LIMITED,
                message="Too many submissions - please wait",
            )
        if response.status_code == 422:
            error_data = response.json()
            error_msg = error_data.get("error", "Plausibility check failed")
            log_debug(
                Component.API,
                "422 plausibility error",
                error_data=error_data,
                payload_sent=signed_payload,
            )
            return SubmissionResult(
                status=SubmissionStatus.PLAUSIBILITY_FAILED,
                message=f"Lap rejected: {error_msg}",
            )
        if response.status_code == 400:
            error_data = response.json()
            error_msg = error_data.get("error", "Validation error")
            log_debug(
                Component.API,
                "400 validation error",
                error_data=error_data,
                payload_sent=signed_payload,
            )
            if isinstance(error_msg, list):
                error_msg = "; ".join(str(error) for error in error_msg)
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message=f"Validation error: {error_msg}",
            )
        if 400 <= response.status_code < 500:
            try:
                error_data = response.json()
            except (ValueError, KeyError, TypeError):
                error_data = {"error": response.text}
            log_debug(
                Component.API,
                "4XX client error",
                status_code=response.status_code,
                error_data=error_data,
                payload_sent=signed_payload,
                headers=dict(response.headers),
            )
            error_msg = error_data.get("error", "Client error") if isinstance(error_data, dict) else str(error_data)
            if isinstance(error_msg, list):
                error_msg = "; ".join(str(error) for error in error_msg)
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message=f"Client error {response.status_code}: {error_msg}",
            )
        return SubmissionResult(
            status=SubmissionStatus.ERROR,
            message=f"Server error: {response.status_code}",
        )

    def _normalize_track_id(self, track_name: str) -> str:
        """
        Normalize track name to ID format.

        Args:
            track_name: Track name from log

        Returns:
            Normalized track ID
        """
        # Remove common suffixes and prefixes
        track_id = track_name.lower()

        # Remove layout suffixes
        for suffix in [" gp", " time attack practice", " practice", " race", " qualify"]:
            if track_id.endswith(suffix):
                track_id = track_id[:-len(suffix)]

        # Remove common prefixes
        for prefix in ["circuit de ", "circuit ", "autodromo ", "autódromo "]:
            if track_id.startswith(prefix):
                track_id = track_id[len(prefix):]

        # Replace spaces with underscores
        track_id = track_id.replace(" ", "_")

        # Remove special characters
        track_id = "".join(c for c in track_id if c.isalnum() or c == "_")

        return track_id

    async def test_connection(self) -> tuple[bool, str]:
        """
        Test connection to the server AND verify secret.

        Returns:
            Tuple of (success, message)
        """
        if not is_secret_configured():
            return False, "APP_SECRET not configured — running in offline mode"

        try:
            client = await self._get_client()

            # First, test basic connectivity
            response = await client.get(f"{self.server_url}/api/tracks")
            if response.status_code != 200 and not (300 <= response.status_code < 400):
                return False, f"Server returned status {response.status_code}"

            # Now test the secret
            secret_ok, secret_msg = await self.test_secret()
            if not secret_ok:
                return False, f"Connected but {secret_msg}"

            return True, "Connected and secret verified"

        except httpx.NetworkError as e:
            detail = str(e).strip() or type(e).__name__
            return False, f"Network error: {detail}"
        except httpx.TimeoutException:
            return False, "Connection timed out"
        except (RuntimeError, OSError, ConnectionError) as e:
            return False, f"Error: {str(e)}"

    async def test_secret(self) -> tuple[bool, str]:
        """
        Test if the embedded secret matches the server's secret.

        Returns:
            Tuple of (success, message)
        """
        if not is_secret_configured():
            return False, "APP_SECRET not configured — running in offline mode"

        try:
            log_debug(Component.API, "test_secret called")
            from .security import create_signature, generate_nonce, get_app_secret, get_timestamp

            # Log only that the secret is present and its length; never log the value
            secret = get_app_secret()
            log_debug(
                Component.API,
                "get_app_secret result",
                secret_configured=bool(secret),
                secret_len=len(secret),
                secret_source=get_secret_source(),
            )

            # Create a test signature with known test values
            timestamp = get_timestamp()
            nonce = generate_nonce()
            log_debug(Component.API, "timestamp", timestamp=timestamp, nonce=nonce[:8])

            # Sign with test payload (must match server expectations)
            signature = create_signature(
                timestamp=timestamp,
                nonce=nonce,
                user_id="test",
                track_id="test",
                lap_time=0,
            )
            log_debug(Component.API, "signature", signature=signature[:20])

            # Send to test endpoint
            client = await self._get_client()
            response = await client.post(
                f"{self.server_url}/api/test-secret",
                json={
                    "_timestamp": timestamp,
                    "_nonce": nonce,
                    "_signature": signature,
                },
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("valid"):
                    return True, "Secret verified"
                else:
                    return False, data.get("error", "Unknown error")
            elif response.status_code == 401:
                return False, "secret mismatch - rebuild client with correct secret"
            elif response.status_code == 500:
                data = response.json()
                return False, data.get("error", "Server error")
            else:
                return False, f"Unexpected status {response.status_code}"

        except (RuntimeError, OSError, ConnectionError, ValueError) as e:
            log_error(Component.API, "test_secret error", error=str(e))
            return False, f"Error testing secret: {str(e)}"

    async def check_for_updates(self) -> dict:
        """
        Check for client updates.

        Returns:
            Dict with update info or empty dict if no update.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.server_url}/api/version")

            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("latestClientVersion")

                if latest_version:
                    # Parse versions
                    try:
                        current_parts = [int(x) for x in VERSION.split(".")]
                        latest_parts = [int(x) for x in latest_version.split(".")]

                        # Compare
                        is_newer = False
                        for i in range(3):
                            c = current_parts[i] if i < len(current_parts) else 0
                            l = latest_parts[i] if i < len(latest_parts) else 0  # noqa: E741
                            if l > c:
                                is_newer = True
                                break
                            if l < c:
                                break

                        if is_newer:
                            return {
                                "available": True,
                                "version": latest_version,
                                "min_version": data.get("minClientVersion"),
                            }
                    except (ValueError, IndexError):
                        pass

            return {"available": False}
        except httpx.NetworkError as e:
            log_error(Component.API, "Update check failed", error=str(e))
            return {"available": False}
        except (RuntimeError, OSError, ConnectionError) as e:
            log_error(Component.API, "Update check failed", error=str(e))
            return {"available": False}

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
