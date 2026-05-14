"""
Security module for SimLaps Client.

Handles payload signing, game process verification, and anti-cheat measures.
"""

import enum
import hmac
import hashlib
import uuid
import time
import os
import sys
from typing import Optional
from dotenv import load_dotenv


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
# APP SECRET - Load from environment
# =============================================================================
# Load .env file if it exists (for development)
# When running as PyInstaller executable, .env is in _MEIPASS directory
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Running as compiled executable - .env is bundled in _MEIPASS
    env_path = os.path.join(sys._MEIPASS, '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        # Fallback: try loading from executable directory
        env_path = os.path.join(os.path.dirname(sys.executable), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
else:
    # Running as script - load from project root
    load_dotenv()

# Production secret for signing payloads
# Matches CLIENT_APP_SECRET in server .env
# Load from environment variable, fallback to None if not set
APP_SECRET = os.environ.get("APP_SECRET")

if not APP_SECRET:
    raise RuntimeError(
        "APP_SECRET environment variable not set. "
        "Please set APP_SECRET in your .env file or environment. "
        "See .env.example for the required format."
    )


def get_app_secret() -> bytes:
    """
    Get the application secret for signing.
    Returns the secret as UTF-8 encoded string bytes (for HMAC compatibility with server).
    """
    return APP_SECRET.encode('utf-8')


# =============================================================================
# GAME PROCESS DETECTION
# =============================================================================

# Known ACE process names
GAME_PROCESS_NAMES = [
    "AssettoCorsaEVO.exe",      # Main game executable
    "AC2-Win64-Shipping.exe",   # Alternative (Unreal shipping build)
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
        for proc in psutil.process_iter(['name']):
            try:
                proc_name = proc.info.get('name', '')
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
        for proc in psutil.process_iter(['name', 'pid', 'create_time']):
            try:
                proc_name = proc.info.get('name', '')
                if proc_name and proc_name in GAME_PROCESS_NAMES:
                    return {
                        'name': proc_name,
                        'pid': proc.info.get('pid'),
                        'start_time': proc.info.get('create_time'),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
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
    signature = hmac.new(
        get_app_secret(),
        sig_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
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
    user_id = str(payload.get('userId', ''))
    track_id = str(payload.get('trackId', ''))
    lap_time = int(payload.get('time', 0))
    
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
        '_timestamp': timestamp,
        '_nonce': nonce,
        '_signature': signature,
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
        timestamp = signed_payload.get('_timestamp', 0)
        nonce = signed_payload.get('_nonce', '')
        signature = signed_payload.get('_signature', '')
        
        user_id = str(signed_payload.get('userId', ''))
        track_id = str(signed_payload.get('trackId', ''))
        lap_time = int(signed_payload.get('time', 0))
        
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
    if os.name != 'nt':
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
                
                print(f"[SECURITY] Steam user detected from registry: {steam64_id} ({username})")
                return steam64_id, username
    except (ImportError, OSError, FileNotFoundError, PermissionError):
        pass
    
    print("[SECURITY] No Steam user found in registry")
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
            with open(loginusers_path, 'r', encoding='utf-8', errors='ignore') as f:
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
    except Exception:
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
        'game_running': game_status.value if isinstance(game_status, GameProcessStatus) else game_status,
        'game_process': game_info,
        'psutil_available': PSUTIL_AVAILABLE,
        'secret_configured': bool(APP_SECRET),
    }
