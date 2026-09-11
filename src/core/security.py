"""
Security module for SimLaps Client.

Handles payload signing, game process verification, and anti-cheat measures.
"""

import enum
import hashlib
import hmac
import os
import sys
import time
import uuid
from typing import Optional

from dotenv import load_dotenv

from ..utils.structured_logger import Component, log_debug


class GameProcessStatus(enum.Enum):
    """Game process detection status."""

    RUNNING = "running"
    NOT_RUNNING = "not_running"
    UNKNOWN = "unknown"


# Try to import psutil, with fallback
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# =============================================================================
# APP SECRET - Load runtime dotenv without packaging it
# =============================================================================
def _load_runtime_dotenv() -> None:
    """Load runtime configuration without overriding process environment.

    Source runs use python-dotenv discovery. Frozen clients only check for an
    external sidecar beside the executable; the PyInstaller extraction
    directory is never treated as a configuration source.
    """
    if getattr(sys, "frozen", False):
        env_path = os.path.join(os.path.dirname(sys.executable), ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path, override=False)
        return
    load_dotenv(override=False)


_load_runtime_dotenv()

# The old .env.example placeholder. Treated as absent so a copied template
# cannot mask a real embedded secret or accidentally enable submissions.
PLACEHOLDER_APP_SECRETS = frozenset({"blahtopsecret"})


def _is_usable_secret(value: Optional[str]) -> bool:
    """Return whether a candidate secret is provisioned and not a placeholder."""
    return bool(value) and value.strip() not in PLACEHOLDER_APP_SECRETS


def _load_embedded_secret() -> Optional[str]:
    """Load the build-time embedded secret from the compiled native module.

    Only release builds contain this module (generated and compiled by
    build.py); source runs fall through to the environment only.
    """
    try:
        import _embedded_secret  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        raw = _embedded_secret.get_secret()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _resolve_secret(env_value: Optional[str], embedded_value: Optional[str]) -> Optional[str]:
    """Pick the effective secret: process env / dotenv wins, embedded is fallback."""
    if _is_usable_secret(env_value):
        return env_value
    if _is_usable_secret(embedded_value):
        return embedded_value
    return None


# Production secret for signing payloads
# Matches CLIENT_APP_SECRET in server .env
APP_SECRET = _resolve_secret(os.environ.get("APP_SECRET"), _load_embedded_secret())


def is_secret_configured() -> bool:
    """Return True when a usable (non-placeholder) APP_SECRET is provisioned."""
    return _is_usable_secret(APP_SECRET)


def get_app_secret() -> bytes:
    """
    Get the application secret for signing.
    Returns the secret as UTF-8 encoded string bytes (for HMAC compatibility with server).
    Raises RuntimeError if APP_SECRET is not configured so the app can run without it
    until a signature is actually required.
    """
    if not _is_usable_secret(APP_SECRET):
        raise RuntimeError(
            "APP_SECRET not provisioned. "
            "Set it in the process environment or a local .env file. "
            "Release builds carry a compiled embedded secret module."
        )
    return APP_SECRET.encode("utf-8")


def get_secret_source() -> str:
    """Return where the effective APP_SECRET came from.

    ``"env"`` covers both the process environment and any dotenv/sidecar
    ``.env`` loaded by :func:`_load_runtime_dotenv`, because both win over the
    embedded secret.  ``"embedded"`` means only the compiled native module
    supplied the secret.  ``"none"`` means no usable secret is provisioned.
    """
    if _is_usable_secret(os.environ.get("APP_SECRET")):
        return "env"
    if _is_usable_secret(_load_embedded_secret()):
        return "embedded"
    return "none"


# =============================================================================
# GAME PROCESS DETECTION
# =============================================================================

# Known ACE process names
GAME_PROCESS_NAMES = [
    "AssettoCorsaEVO.exe",  # Main game executable
    "AC2-Win64-Shipping.exe",  # Alternative (Unreal shipping build)
]


def is_game_running() -> GameProcessStatus:
    """
    Check if Assetto Corsa Evo is currently running.

    This prevents log file manipulation when the game isn't running.

    Returns:
        GameProcessStatus.RUNNING if ACE process is detected
        GameProcessStatus.NOT_RUNNING if process not found
        GameProcessStatus.UNKNOWN if detection failed (psutil unavailable or error)
    """
    if not PSUTIL_AVAILABLE:
        # If psutil not available, detection is uncertain
        return GameProcessStatus.UNKNOWN

    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info.get("name", "")
                if proc_name and proc_name in GAME_PROCESS_NAMES:
                    return GameProcessStatus.RUNNING
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Process disappeared or we can't access it
                continue
    except Exception:
        # On any error, detection is uncertain
        return GameProcessStatus.UNKNOWN

    return GameProcessStatus.NOT_RUNNING


def get_game_process_info() -> Optional[dict]:
    """
    Get information about the running ACE process.

    Returns:
        Dict with process info if found, None otherwise
    """
    if not PSUTIL_AVAILABLE:
        return None

    try:
        for proc in psutil.process_iter(["name", "pid", "create_time"]):
            try:
                proc_name = proc.info.get("name", "")
                if proc_name and proc_name in GAME_PROCESS_NAMES:
                    return {
                        "name": proc_name,
                        "pid": proc.info.get("pid"),
                        "start_time": proc.info.get("create_time"),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:  # noqa: S110
        pass

    return None


# =============================================================================
# PAYLOAD SIGNING
# =============================================================================


def generate_nonce() -> str:
    """Generate a unique nonce for replay prevention."""
    return str(uuid.uuid4())


def get_timestamp() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


def create_signature(
    timestamp: int,
    nonce: str,
    user_id: str,
    track_id: str,
    lap_time: int,
) -> str:
    """
    Create HMAC-SHA256 signature for a lap submission.

    Args:
        timestamp: Unix timestamp in milliseconds
        nonce: Unique submission identifier
        user_id: Steam ID of the user
        track_id: Track identifier
        lap_time: Lap time in milliseconds

    Returns:
        Hex-encoded signature string
    """
    # Create the signature data string
    # Order matters - must match server verification
    sig_data = f"{timestamp}:{nonce}:{user_id}:{track_id}:{lap_time}"

    # Create HMAC-SHA256 signature
    signature = hmac.new(get_app_secret(), sig_data.encode("utf-8"), hashlib.sha256).hexdigest()

    return signature


def sign_payload(payload: dict) -> dict:
    """
    Sign a lap submission payload.

    Adds timestamp, nonce, and signature to the payload for server verification.

    Args:
        payload: The lap data to sign (must contain userId, trackId, time)

    Returns:
        New dict with original payload plus security fields
    """
    timestamp = get_timestamp()
    nonce = generate_nonce()

    # Extract required fields for signature
    user_id = str(payload.get("userId", ""))
    track_id = str(payload.get("trackId", ""))
    lap_time = int(payload.get("time", 0))

    # Create signature
    signature = create_signature(
        timestamp=timestamp,
        nonce=nonce,
        user_id=user_id,
        track_id=track_id,
        lap_time=lap_time,
    )

    # Return payload with security fields
    return {
        **payload,
        "_timestamp": timestamp,
        "_nonce": nonce,
        "_signature": signature,
    }


def verify_signature_locally(signed_payload: dict) -> bool:
    """
    Verify a signed payload locally (for testing).

    Args:
        signed_payload: Payload with _timestamp, _nonce, _signature

    Returns:
        True if signature is valid
    """
    try:
        timestamp = signed_payload.get("_timestamp", 0)
        nonce = signed_payload.get("_nonce", "")
        signature = signed_payload.get("_signature", "")

        user_id = str(signed_payload.get("userId", ""))
        track_id = str(signed_payload.get("trackId", ""))
        lap_time = int(signed_payload.get("time", 0))

        expected = create_signature(
            timestamp=timestamp,
            nonce=nonce,
            user_id=user_id,
            track_id=track_id,
            lap_time=lap_time,
        )

        # Use constant-time comparison
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


# =============================================================================
# STEAM USER DETECTION
# =============================================================================


def get_steam_user() -> tuple[Optional[str], Optional[str]]:
    """
    Get the currently logged-in Steam user from Windows Registry.

    Steam stores the active user info in the registry when running.

    Returns:
        Tuple of (steam_id, username) or (None, None) if not found
    """
    if os.name != "nt":
        return None, None

    try:
        import winreg

        # Steam stores active user in HKEY_CURRENT_USER\Software\Valve\Steam
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            # ActiveUser contains the Steam3 ID (32-bit account ID)
            active_user, _ = winreg.QueryValueEx(key, "ActiveUser")

            if active_user and active_user != 0:
                # Convert Steam3 ID to Steam64 ID
                # Steam64 = Steam3 + 76561197960265728
                steam64_id = str(active_user + 76561197960265728)

                # Try to get the username from loginusers.vdf or registry
                username = _get_steam_username(steam64_id)

                log_debug(Component.SECURITY, f"Steam user detected from registry: {steam64_id} ({username})")
                return steam64_id, username
    except (ImportError, OSError, FileNotFoundError, PermissionError):
        pass

    log_debug(Component.SECURITY, "No Steam user found in registry")
    return None, None


def _get_steam_username(steam64_id: str) -> Optional[str]:
    """
    Try to get Steam username for a given Steam64 ID.

    Checks Steam's loginusers.vdf file for cached usernames.
    """
    try:
        import winreg

        # Get Steam install path
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")

        # Parse loginusers.vdf for username
        loginusers_path = os.path.join(steam_path, "config", "loginusers.vdf")

        if os.path.exists(loginusers_path):
            with open(loginusers_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

                # Simple VDF parsing - look for the steam64 ID and then PersonaName
                # Format is like: "76561198321627695" { "AccountName" "..." "PersonaName" "Glebulon" }
                import re

                # Find the block for this user
                pattern = rf'"{steam64_id}"\s*\{{\s*([^}}]+)\}}'
                match = re.search(pattern, content, re.DOTALL)

                if match:
                    user_block = match.group(1)
                    # Extract PersonaName
                    persona_match = re.search(r'"PersonaName"\s+"([^"]+)"', user_block)
                    if persona_match:
                        return persona_match.group(1)
    except Exception:  # noqa: S110
        pass

    return None


# =============================================================================
# ANTI-CHEAT UTILITIES
# =============================================================================


def get_security_status() -> dict:
    """
    Get current security status for display in UI.

    Returns:
        Dict with security-related status information
    """
    game_status = is_game_running()
    game_info = get_game_process_info() if game_status == GameProcessStatus.RUNNING else None

    return {
        "game_running": game_status.value if isinstance(game_status, GameProcessStatus) else game_status,
        "game_process": game_info,
        "psutil_available": PSUTIL_AVAILABLE,
        "secret_configured": bool(APP_SECRET),
    }
