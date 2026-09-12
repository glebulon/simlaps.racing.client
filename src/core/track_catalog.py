"""Static track catalog and profile selection helpers for telemetry analysis."""

import json
import os
from importlib import resources
from pathlib import Path
from typing import Optional

# Path to track catalog JSON file
_CATALOG_PATH = Path(__file__).parent / "data" / "track_catalog.json"


def _load_catalog() -> dict:
    """Load track catalog from JSON file with schema validation."""
    try:
        catalog_text = resources.files("src.core").joinpath("data", "track_catalog.json").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
        # Keep source checkouts and PyInstaller bundles working when the
        # package resource loader is not available for the active importer.
        if not _CATALOG_PATH.exists():
            raise FileNotFoundError(f"Track catalog not found at {_CATALOG_PATH}")  # noqa: B904
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

            calibration = config.get("_calibration")
            if calibration is not None:
                if not isinstance(calibration, dict):
                    raise ValueError(
                        f"Calibration in config '{config_key}' in track '{track_key}' must be a dictionary"
                    )
                if any(not isinstance(key, str) or not isinstance(value, str) for key, value in calibration.items()):
                    raise ValueError(
                        f"Calibration fields in config '{config_key}' in track '{track_key}' must be strings"
                    )

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


def _normalize_label(value: object) -> str:
    """Normalize ACE labels while retaining their token order."""
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value.casefold() if char.isalnum())


def _track_labels(track_key: str, track: dict) -> list[str]:
    return [track_key, track.get("name", ""), *track.get("aliases", [])]


def _config_labels(config_key: str, config: dict) -> list[str]:
    return [config_key, config.get("name", ""), *config.get("aliases", [])]


def _matches(value: object, labels: list[str]) -> bool:
    normalized = _normalize_label(value)
    return bool(normalized) and normalized in {
        _normalize_label(label) for label in labels if _normalize_label(label)
    }


def _matching_track_keys(track_name: str, config_name: Optional[str]) -> list[str]:
    """Find exact track identities, including split track/config catalog rows."""
    matches: list[str] = []
    normalized_track = _normalize_label(track_name)
    normalized_config = _normalize_label(config_name)
    for track_key, track in TRACK_CATALOG.items():
        labels = _track_labels(track_key, track)
        exact_track = _matches(track_name, labels)
        combined_track = bool(
            normalized_config
            and any(
                normalized_track + normalized_config == _normalize_label(label)
                for label in labels
            )
        )
        if exact_track or combined_track:
            matches.append(track_key)
    return matches


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
    overall_confidence = "estimated" if any(_corner_confidence(c) == "estimated" for c in corners) else "profiled"
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
        matching_keys = _matching_track_keys(track_name, config_name)
        if config_name:
            # An explicit layout is a constraint.  Do not silently use a
            # default layout when it is unknown or conflicts with the track.
            for track_key in matching_keys:
                track = TRACK_CATALOG[track_key]
                for config_key, config in track["configs"].items():
                    if _matches(config_name, _config_labels(config_key, config)):
                        return track_key, build_track_profile(track_key, config_key)
            return None, None

        if matching_keys:
            track_key = matching_keys[0]
            return track_key, build_track_profile(
                track_key, TRACK_CATALOG[track_key]["default_config"]
            )
        return None, None

    if path:
        path_l = _normalize_label(os.path.normpath(path))
        path_matches = []
        for track_key, track in TRACK_CATALOG.items():
            labels = [_normalize_label(label) for label in _track_labels(track_key, track)]
            matched_labels = [label for label in labels if label and label in path_l]
            if matched_labels:
                # Rank only labels that actually matched the path.  Ranking
                # every alias lets an unrelated long alias hide a specific
                # layout such as ``suzuka_east`` or ``nurburgring_gp``.
                path_matches.append((max(map(len, matched_labels)), track_key, track))
        for _, track_key, track in sorted(path_matches, reverse=True):
            if config_name:
                for config_key, config in track["configs"].items():
                    if _matches(config_name, _config_labels(config_key, config)):
                        return track_key, build_track_profile(track_key, config_key)
                continue
            for config_key, config in track["configs"].items():
                config_labels = [_normalize_label(label) for label in _config_labels(config_key, config)]
                if any(label and label in path_l for label in config_labels):
                    return track_key, build_track_profile(track_key, config_key)
            return track_key, build_track_profile(track_key, track["default_config"])
        if config_name:
            return None, None

    return None, None


def find_track_by_name(track_name: str) -> tuple:
    """Find a track by name or alias, return (key, profile)."""
    if not track_name:
        return None, None

    track_key, profile = select_track_profile(track_name=track_name)
    if profile:
        return track_key, profile

    # Preserve the legacy prefix helper for path-like callers, choosing the
    # most specific alias so ``suzuka_east`` cannot be claimed by ``suzuka``.
    normalized_name = _normalize_label(track_name)
    prefix_matches = []
    for key, track in TRACK_CATALOG.items():
        for label in _track_labels(key, track):
            normalized_label = _normalize_label(label)
            if normalized_label and normalized_name.startswith(normalized_label):
                prefix_matches.append((len(normalized_label), key))
    if prefix_matches:
        _, key = max(prefix_matches)
        return key, build_track_profile(key, TRACK_CATALOG[key]["default_config"])

    return None, None
