"""Static track catalog and profile selection helpers for telemetry analysis."""

import json
import os
from importlib import resources
from pathlib import Path
from typing import Optional, Tuple


# Path to track catalog JSON file
_CATALOG_PATH = Path(__file__).parent / "data" / "track_catalog.json"


def _load_catalog() -> dict:
    """Load track catalog from JSON file with schema validation."""
    try:
        catalog_text = resources.files("src.core").joinpath(
            "data", "track_catalog.json"
        ).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
        # Keep source checkouts and PyInstaller bundles working when the
        # package resource loader is not available for the active importer.
        if not _CATALOG_PATH.exists():
            raise FileNotFoundError(f"Track catalog not found at {_CATALOG_PATH}")
        catalog_text = _CATALOG_PATH.read_text(encoding="utf-8")

    catalog = json.loads(catalog_text)
    
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


def _corner_confidence(corner: dict) -> str:
    """Extract confidence from a corner dict, defaulting to 'profiled'."""
    return corner.get("_confidence", "profiled")


def build_track_profile(track_key: str, config_key: str) -> dict:
    """Build a track profile dictionary.

    Returns a dict with keys:
        track_key, track_name, config_key, config_name, display_name,
        corners (list), confidence (str) — the overall profile confidence
        is "estimated" if ANY corner in the profile is estimated.
    """
    track = TRACK_CATALOG[track_key]
    config = track["configs"][config_key]
    corners = config.get("corners", [])
    # Overall profile confidence: "estimated" if any corner is estimated
    overall_confidence = "estimated" if any(
        _corner_confidence(c) == "estimated" for c in corners
    ) else "profiled"
    return {
        "track_key": track_key,
        "track_name": track["name"],
        "config_key": config_key,
        "config_name": config["name"],
        "display_name": f"{track['name']} ({config['name']})",
        "corners": corners,
        "confidence": overall_confidence,
    }


def select_track_profile(
    path: Optional[str] = None,
    track_name: Optional[str] = None,
    config_name: Optional[str] = None,
) -> tuple:
    """Select a track profile based on path, track name, or config name.

    Returns:
        tuple: (track_key, track_profile) or (None, None) if not found
    """
    if track_name:
        # Collect every catalog entry whose key or aliases match the name.
        # A single track name can map to several entries (e.g. "Nurburgring"
        # matches both the Nordschleife and GP catalogs), so we cannot stop
        # at the first hit when a specific config is requested.
        matching_keys: list[str] = []
        if track_name in TRACK_CATALOG:
            matching_keys.append(track_name)
        tn_lower = track_name.lower()
        for track_key, track in TRACK_CATALOG.items():
            if track_key == track_name:
                continue
            labels = [track_key, *track.get("aliases", [])]
            if tn_lower in [label.lower() for label in labels]:
                matching_keys.append(track_key)

        # Prefer a config whose key/aliases match config_name across ALL
        # matching tracks before falling back to any default.
        if config_name:
            cfg_lower = config_name.lower()
            for track_key in matching_keys:
                track = TRACK_CATALOG[track_key]
                for config_key, config in track["configs"].items():
                    config_labels = [config_key, *config.get("aliases", [])]
                    if cfg_lower in [label.lower() for label in config_labels]:
                        return track_key, build_track_profile(track_key, config_key)

        # Fall back to the default config of the first matching track.
        if matching_keys:
            track_key = matching_keys[0]
            return track_key, build_track_profile(track_key, TRACK_CATALOG[track_key]["default_config"])

        return None, None

    if path:
        path_l = os.path.normpath(path).lower()
        for track_key, track in TRACK_CATALOG.items():
            if any(alias in path_l for alias in track.get("aliases", [])):
                if config_name:
                    cfg_lower = config_name.lower()
                    for config_key, config in track["configs"].items():
                        config_labels = [config_key, *config.get("aliases", [])]
                        if cfg_lower in [label.lower() for label in config_labels]:
                            return track_key, build_track_profile(track_key, config_key)
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
