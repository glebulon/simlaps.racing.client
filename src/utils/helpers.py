"""
Helper utilities for SimLaps Client.
"""

from typing import Optional


def format_lap_time(time_ms: int) -> str:
    """
    Format lap time from milliseconds to human-readable string.
    
    Args:
        time_ms: Lap time in milliseconds
        
    Returns:
        Formatted string like "2:18.456" or "58.123"
    """
    if time_ms <= 0:
        return "--:--.---"
    
    total_seconds = time_ms / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    
    if minutes > 0:
        return f"{minutes}:{seconds:06.3f}"
    else:
        return f"{seconds:.3f}"


def format_sector_time(time_ms: Optional[int]) -> str:
    """
    Format sector time from milliseconds.
    
    Args:
        time_ms: Sector time in milliseconds, or None
        
    Returns:
        Formatted string like "45.123" or "-"
    """
    if time_ms is None or time_ms <= 0:
        return "-"
    
    seconds = time_ms / 1000
    return f"{seconds:.3f}"


def format_fuel(fuel: Optional[float]) -> str:
    """
    Format fuel consumption.
    
    Args:
        fuel: Fuel used in liters, or None
        
    Returns:
        Formatted string like "2.5 L" or "-"
    """
    if fuel is None:
        return "-"
    return f"{fuel:.2f} L"


def truncate_string(text: str, max_length: int = 30) -> str:
    """
    Truncate a string to max length with ellipsis.
    
    Args:
        text: Input string
        max_length: Maximum length including ellipsis
        
    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def format_track_name(track_id: str) -> str:
    """
    Convert track ID to display name.
    
    Args:
        track_id: Internal track identifier
        
    Returns:
        Human-readable track name
    """
    # Common track name mappings
    track_names = {
        "spa": "Spa-Francorchamps",
        "spa_francorchamps": "Spa-Francorchamps",
        "monza": "Monza",
        "imola": "Imola",
        "barcelona": "Barcelona",
        "silverstone": "Silverstone",
        "nurburgring": "Nürburgring",
        "brands_hatch": "Brands Hatch",
        "paul_ricard": "Paul Ricard",
        "misano": "Misano",
        "zandvoort": "Zandvoort",
        "hungaroring": "Hungaroring",
        "kyalami": "Kyalami",
        "laguna_seca": "Laguna Seca",
        "suzuka": "Suzuka",
        "mount_panorama": "Mount Panorama",
    }
    
    # Try direct lookup
    lower_track = track_id.lower()
    for key, name in track_names.items():
        if key in lower_track:
            return name
    
    # Fallback: clean up the track ID
    return track_id.replace("_", " ").title()


def format_car_name(car_id: str) -> str:
    """
    Convert car ID to display name.
    
    Args:
        car_id: Internal car identifier (e.g., "ks_porsche_992_gt3_cup")
        
    Returns:
        Human-readable car name
    """
    # Remove common prefixes
    name = car_id
    for prefix in ["ks_", "ac_", "rss_", "vrc_"]:
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    
    # Replace underscores and capitalize
    name = name.replace("_", " ")
    
    # Capitalize properly
    words = []
    for word in name.split():
        # Keep known abbreviations uppercase
        if word.upper() in ["GT3", "GT4", "GTE", "LMP", "DTM", "CUP", "EVO", "RSR", "GT"]:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    
    return " ".join(words)
