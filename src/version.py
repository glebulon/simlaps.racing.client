"""
SimLaps Client Version Information

Single source of truth for version numbers.
"""

# Game name (for client display - bottom right)
GAME_NAME = "SimLaps Client"

# Game display name (for game version display - top left)  
GAME_DISPLAY_NAME = "AC EVO"

# Version components
VERSION_MAJOR = 1
VERSION_MINOR = 3
VERSION_PATCH = 0

# Full version string
VERSION = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}"

# Build metadata (set during build process)
BUILD_DATE = None
BUILD_COMMIT = None

# Minimum compatible server API version
MIN_SERVER_VERSION = "1.1.1"

# User-Agent string for API requests
USER_AGENT = f"SimLaps-Client/{VERSION}"


def get_version() -> str:
    """Get the version string."""
    return VERSION


def get_version_tuple() -> tuple[int, int, int]:
    """Get version as tuple for comparison."""
    return (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH)

