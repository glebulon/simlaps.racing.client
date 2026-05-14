"""Static track catalog and profile selection helpers for telemetry analysis."""

import json
import os
from pathlib import Path
from typing import Optional, Tuple


# Path to track catalog JSON file
_CATALOG_PATH = Path(__file__).parent / "data" / "track_catalog.json"


def _load_catalog() -> dict:
    """Load track catalog from JSON file with schema validation."""
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(f"Track catalog not found at {_CATALOG_PATH}")
    
    with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    
    # Schema validation
    _validate_catalog(catalog)
    
    return catalog


def _validate_catalog(catalog: dict) -> None:
    """Validate catalog structure and required fields."""
    if not isinstance(catalog, dict):
        raise ValueError("Catalog must be a dictionary")
    
    for track_key, track in catalog.items():
        if not isinstance(track, dict):
            raise ValueError(f"Track '{track_key}' must be a dictionary")
        
        # Required track fields
        required_fields = ["name", "aliases", "default_config", "configs"]
        for field in required_fields:
            if field not in track:
                raise ValueError(f"Track '{track_key}' missing required field: {field}")
        
        # Validate configs
        if not isinstance(track["configs"], dict):
            raise ValueError(f"Track '{track_key}' configs must be a dictionary")
        
        for config_key, config in track["configs"].items():
            if not isinstance(config, dict):
                raise ValueError(f"Config '{config_key}' in track '{track_key}' must be a dictionary")
            
            # Validate corners
            if "corners" in config:
                if not isinstance(config["corners"], list):
                    raise ValueError(f"Corners in config '{config_key}' must be a list")
                
                for corner in config["corners"]:
                    if not isinstance(corner, dict):
                        raise ValueError(f"Corner must be a dictionary in config '{config_key}'")
                    
                    required_corner_fields = ["id", "name", "start", "end"]
                    for field in required_corner_fields:
                        if field not in corner:
                            raise ValueError(f"Corner missing required field: {field}")


# Load catalog on module import
TRACK_CATALOG = _load_catalog()


def build_track_profile(track_key: str, config_key: str) -> dict:
    """Build a track profile dictionary."""
    track = TRACK_CATALOG[track_key]
    config = track["configs"][config_key]
    return {
        "track_key": track_key,
        "track_name": track["name"],
        "config_key": config_key,
        "config_name": config["name"],
        "display_name": f"{track['name']} ({config['name']})",
        "corners": config.get("corners", []),
    }


def select_track_profile(
    path: str = None,
    track_name: str = None,
    config_name: str = None
) -> tuple:
    """Select a track profile based on path, track name, or config name.

    Returns:
        tuple: (track_key, track_profile) or (None, None) if not found
    """
    if track_name:
        # Direct match first
        if track_name in TRACK_CATALOG:
            track = TRACK_CATALOG[track_name]
            return track_name, build_track_profile(track_name, track["default_config"])

        # Search aliases
        for track_key, track in TRACK_CATALOG.items():
            labels = [track_key, *track.get("aliases", [])]
            if track_name.lower() in [l.lower() for l in labels]:
                if config_name:
                    for config_key, config in track["configs"].items():
                        config_labels = [config_key, *config.get("aliases", [])]
                        if config_name.lower() in [l.lower() for l in config_labels]:
                            return track_key, build_track_profile(track_key, config_key)
                return track_key, build_track_profile(track_key, track["default_config"])
        return None, None

    if path:
        path_l = os.path.normpath(path).lower()
        for track_key, track in TRACK_CATALOG.items():
            if any(alias in path_l for alias in track.get("aliases", [])):
                for config_key, config in track["configs"].items():
                    if any(alias in path_l for alias in config.get("aliases", [])):
                        return track_key, build_track_profile(track_key, config_key)
                return track_key, build_track_profile(track_key, track["default_config"])

    return None, None


def find_track_by_name(track_name: str) -> tuple:
    """Find a track by name or alias, return (key, profile)."""
    if not track_name:
        return None, None

    # Direct key match
    if track_name in TRACK_CATALOG:
        return track_name, build_track_profile(track_name, TRACK_CATALOG[track_name]["default_config"])

    # Search in aliases (case-insensitive)
    track_name_lower = track_name.lower().replace("_", "-").replace(" ", "-")
    for track_key, track in TRACK_CATALOG.items():
        for alias in track.get("aliases", []):
            alias_normalized = alias.lower().replace("_", "-").replace(" ", "-")
            if track_name_lower == alias_normalized or track_name_lower.startswith(alias_normalized):
                return track_key, build_track_profile(track_key, track["default_config"])

    return None, None
