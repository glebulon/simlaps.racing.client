"""
API Client for SimLaps server communication.

Handles lap time submissions with signed payloads for anti-cheat.
No API key required - uses embedded app secret for signing.
"""

import httpx
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from ..models import SessionData, LapData
from ..utils.debug_logger import DebugLogger
from .security import sign_payload, is_game_running
from ..version import VERSION, USER_AGENT


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
    ):
        """
        Initialize API client.
        
        Args:
            server_url: Base URL of the SimLaps server
        """
        self.server_url = (server_url or self.DEFAULT_SERVER_URL).rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._debug = DebugLogger()

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
        # Reset client
        if self._client:
            self._client = None

    async def submit_lap(
        self,
        session: SessionData,
        lap: LapData,
        user_id: Optional[str] = None,
        submit_invalid: bool = False,
    ) -> SubmissionResult:
        """
        Submit a completed lap to the server.
        
        The payload is cryptographically signed for anti-cheat verification.
        
        Args:
            session: Session data containing track, car info
            lap: The completed lap data
            user_id: Override user ID (Steam ID)
            submit_invalid: If True, submit even if lap is invalid
            
        Returns:
            SubmissionResult with status and details
        """
        self._debug.log(f"[API] submit_lap called")
        self._debug.log(f"  lap_time: {lap.lap_time_str} ({lap.lap_time_ms}ms)")
        self._debug.log(f"  is_valid: {lap.is_valid}")
        self._debug.log(f"  submit_invalid setting: {submit_invalid}")
        
        # Anti-cheat: Verify game is running before submission
        game_running = is_game_running()
        self._debug.log(f"  is_game_running: {game_running}")
        if not game_running:
            self._debug.log(f"[API] Rejected: Game not running")
            return SubmissionResult(
                status=SubmissionStatus.GAME_NOT_RUNNING,
                message="Game must be running to submit laps",
            )

        # Don't submit invalid laps unless explicitly requested
        if not lap.is_valid and not submit_invalid:
            self._debug.log(f"[API] Rejected: Invalid lap and submit_invalid=False")
            return SubmissionResult(
                status=SubmissionStatus.INVALID_LAP,
                message="Lap was invalidated (penalty or off-track)",
            )

        # Validate we have a user ID
        final_user_id = user_id or session.player_id
        self._debug.log(f"  user_id param: {user_id}")
        self._debug.log(f"  session.player_id: {session.player_id}")
        self._debug.log(f"  final_user_id: {final_user_id}")
        if not final_user_id:
            self._debug.log(f"[API] Rejected: No user ID")
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message="No Steam ID detected - please start a session in game",
            )

        # Build submission payload
        track_id = self._normalize_track_id(session.track)
        self._debug.log(f"  track: {session.track} -> {track_id}")
        self._debug.log(f"  car: {session.car}")
        self._debug.log(f"  lap_time_ms: {lap.lap_time_ms}")
        
        # Ensure time is int and positive
        final_time = int(lap.lap_time_ms)
        if final_time <= 0:
             self._debug.log(f"[API] Rejected: Invalid lap time {final_time}")
             return SubmissionResult(
                 status=SubmissionStatus.INVALID_LAP,
                 message="Invalid lap time (<= 0)",
             )

        payload = {
            "userId": final_user_id,
            "trackId": track_id,
            "carId": session.car,
            "time": final_time,
            "sessionId": session.session_id,  # Links laps from the same session
            "sessionType": session.session_type,  # practice, qualifying, race, etc.
            "gameVersion": session.game_version,
            "tires": lap.tyre_compound,
            "valid": lap.is_valid,  # False if lap had penalties/off-track
        }

        # Add sector times if available and positive (server rejects 0)
        if lap.sector1_ms is not None and int(lap.sector1_ms) > 0:
            payload["sector1"] = int(lap.sector1_ms)
        if lap.sector2_ms is not None and int(lap.sector2_ms) > 0:
            payload["sector2"] = int(lap.sector2_ms)
        if lap.sector3_ms is not None and int(lap.sector3_ms) > 0:
            payload["sector3"] = int(lap.sector3_ms)

        # Add fuel if available and valid
        if lap.fuel_used is not None:
            try:
                fuel_value = float(lap.fuel_used)
                if fuel_value >= 0:  # Ensure non-negative fuel values
                    payload["fuelUsed"] = fuel_value
            except (ValueError, TypeError):
                # Skip invalid fuel values
                pass

        # Add setup notes (serialized session setup map) if available.
        if session.setup_notes:
            setup_notes = session.setup_notes.strip()
            if setup_notes:
                payload["setupNotes"] = setup_notes

        # Sign the payload (adds _timestamp, _nonce, _signature)
        signed_payload = sign_payload(payload)
        
        self._debug.log(f"[API] Sending to {self.server_url}{self.SUBMIT_ENDPOINT}")
        self._debug.log(f"[API] Payload keys: {list(signed_payload.keys())}")

        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.server_url}{self.SUBMIT_ENDPOINT}",
                json=signed_payload,
            )
            
            self._debug.log(f"[API] Response status: {response.status_code}")

            if response.status_code == 201:
                data = response.json()
                self._debug.log(f"[API] SUCCESS! Lap ID: {data.get('id')}")
                return SubmissionResult(
                    status=SubmissionStatus.SUCCESS,
                    message="Lap submitted successfully",
                    lap_id=data.get("id"),
                )
            elif response.status_code == 401:
                # Signature verification failed
                error_data = response.json() if response.content else {}
                self._debug.log(f"[API] 401 error response: {error_data}")
                return SubmissionResult(
                    status=SubmissionStatus.SIGNATURE_ERROR,
                    message="Signature verification failed - please update the app",
                )
            elif response.status_code == 409:
                # Could be duplicate nonce (replay) or duplicate lap
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
            elif response.status_code == 429:
                return SubmissionResult(
                    status=SubmissionStatus.RATE_LIMITED,
                    message="Too many submissions - please wait",
                )
            elif response.status_code == 422:
                # Plausibility check failed - add comprehensive logging
                error_data = response.json()
                error_msg = error_data.get("error", "Plausibility check failed")
                
                # Detailed logging for server-side validation errors
                self._debug.log(f"[API] 422 ERROR - Server rejected submission")
                self._debug.log(f"[API] Error data: {error_data}")
                self._debug.log(f"[API] Payload sent: {signed_payload}")
                
                return SubmissionResult(
                    status=SubmissionStatus.PLAUSIBILITY_FAILED,
                    message=f"Lap rejected: {error_msg}",
                )
            elif response.status_code == 400:
                # Validation error - add comprehensive logging
                error_data = response.json()
                error_msg = error_data.get("error", "Validation error")
                
                # Detailed logging for server-side validation errors
                self._debug.log(f"[API] 400 ERROR - Server validation failed")
                self._debug.log(f"[API] Error data: {error_data}")
                self._debug.log(f"[API] Payload sent: {signed_payload}")
                
                if isinstance(error_msg, list):
                    error_msg = "; ".join(str(e) for e in error_msg)
                return SubmissionResult(
                    status=SubmissionStatus.ERROR,
                    message=f"Validation error: {error_msg}",
                )
            elif 400 <= response.status_code < 500:
                # Generic 4xx error handling with comprehensive logging
                error_data = {}
                try:
                    error_data = response.json()
                except (ValueError, KeyError, TypeError):
                    error_data = {"error": response.text}
                
                self._debug.log(f"[API] 4XX ERROR - Status {response.status_code}")
                self._debug.log(f"[API] Error data: {error_data}")
                self._debug.log(f"[API] Payload sent: {signed_payload}")
                self._debug.log(f"[API] Response headers: {dict(response.headers)}")
                
                error_msg = error_data.get("error", "Client error") if isinstance(error_data, dict) else str(error_data)
                if isinstance(error_msg, list):
                    error_msg = "; ".join(str(e) for e in error_msg)
                
                return SubmissionResult(
                    status=SubmissionStatus.ERROR,
                    message=f"Client error {response.status_code}: {error_msg}",
                )
            else:
                return SubmissionResult(
                    status=SubmissionStatus.ERROR,
                    message=f"Server error: {response.status_code}",
                )

        except httpx.NetworkError as e:
            self._debug.log(f"[API] Network error: {e}")
            return SubmissionResult(
                status=SubmissionStatus.NETWORK_ERROR,
                message=f"Network error: {str(e)}",
            )
        except httpx.TimeoutException:
            self._debug.log(f"[API] Timeout")
            return SubmissionResult(
                status=SubmissionStatus.NETWORK_ERROR,
                message="Request timed out",
            )
        except (RuntimeError, OSError, ConnectionError) as e:
            import traceback
            self._debug.log(f"[API] Exception: {e}")
            self._debug.log(f"[API] Traceback: {traceback.format_exc()}")
            return SubmissionResult(
                status=SubmissionStatus.ERROR,
                message=f"Unexpected error: {str(e)}",
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
            return False, f"Network error: {str(e)}"
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
        try:
            self._debug.log("[API] test_secret called")
            from .security import create_signature, get_timestamp, generate_nonce, get_app_secret
            
            # Log the decoded secret
            secret = get_app_secret()
            self._debug.log(f"[API] get_app_secret() = {secret[:20]}... (len={len(secret)})")
            
            # Create a test signature with known test values
            timestamp = get_timestamp()
            nonce = generate_nonce()
            self._debug.log(f"[API] timestamp={timestamp}, nonce={nonce[:8]}...")
            
            # Sign with test payload (must match server expectations)
            signature = create_signature(
                timestamp=timestamp,
                nonce=nonce,
                user_id='test',
                track_id='test',
                lap_time=0,
            )
            self._debug.log(f"[API] signature = {signature[:20]}...")
            
            # Send to test endpoint
            client = await self._get_client()
            response = await client.post(
                f"{self.server_url}/api/test-secret",
                json={
                    '_timestamp': timestamp,
                    '_nonce': nonce,
                    '_signature': signature,
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('valid'):
                    return True, "Secret verified"
                else:
                    return False, data.get('error', 'Unknown error')
            elif response.status_code == 401:
                return False, "secret mismatch - rebuild client with correct secret"
            elif response.status_code == 500:
                data = response.json()
                return False, data.get('error', 'Server error')
            else:
                return False, f"Unexpected status {response.status_code}"
                
        except (RuntimeError, OSError, ConnectionError, ValueError) as e:
            self._debug.log(f"[API] test_secret error: {e}")
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
                            l = latest_parts[i] if i < len(latest_parts) else 0
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
            self._debug.log(f"[API] Update check failed: {e}")
            return {"available": False}
        except (RuntimeError, OSError, ConnectionError) as e:
            self._debug.log(f"[API] Update check failed: {e}")
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
