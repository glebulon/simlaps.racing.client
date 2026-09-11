"""
Configuration Manager for SimLaps Client.

Handles persistent settings stored in AppData.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.core.security import is_secret_configured
from src.utils.structured_logger import Component, log_warning

# Default configuration values
DEFAULT_LOG_PATH = str(Path.home() / "Saved Games" / "ACE" / "Logs")
DEFAULT_SERVER_URL = "https://simlaps.racing"
APP_NAME = "SimLapsClient"

# Config version for schema migration support.
# Increment when fields are renamed, removed, or have breaking changes.
CONFIG_VERSION = 1

# Maps legacy (old) field names to their current replacements.
# When a config dict contains a legacy key, it is transparently renamed
# to the current key during load. Remove entries once the oldest supported
# config version no longer emits them.
_LEGACY_FIELD_MAP: dict[str, str] = {
    # Example: "discord_post_invalid": "submit_invalid_laps",
}


def get_config_dir() -> Path:
    """Get the configuration directory path."""
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    else:  # macOS/Linux
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))

    config_dir = Path(base) / APP_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    """Get the configuration file path.

    When APP_SECRET is not configured (debug mode), a separate
    ``config-debug.json`` file is used so debug settings don't
    overwrite the production config.
    """
    filename = "config-debug.json" if not is_secret_configured() else "config.json"
    return get_config_dir() / filename


@dataclass
class AppConfig:
    """Application configuration settings."""

    # Schema version — incremented when fields are added/renamed/removed.
    # Persisted in the JSON file so from_dict() can run migrations.
    config_version: int = CONFIG_VERSION

    # Paths
    log_path: str = field(default_factory=lambda: DEFAULT_LOG_PATH)

    # Server
    server_url: str = DEFAULT_SERVER_URL

    # Behavior
    auto_submit: bool = True
    submit_invalid_laps: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    start_with_windows: bool = False

    # UI
    theme: str = "dark"
    window_width: int = 500
    window_height: int = 700
    window_x: Optional[int] = None
    window_y: Optional[int] = None

    # History
    max_history_items: int = 100

    # Discord Integration
    discord_webhook_url: Optional[str] = None
    discord_enabled: bool = False
    discord_pb_only: bool = True

    # Telemetry
    telemetry_enabled: bool = False
    telemetry_output_path: str = field(default_factory=lambda: str(Path.home() / "Documents" / "SimLaps" / "Telemetry"))
    # When False (default), suppress on-disk debug artefacts produced by the
    # telemetry capture: ``telemetry_diagnostics_*.log``, ``capture_*.jsonl``,
    # and ``raw_dump_*.jsonl``. The summary HTML / AI prompt / analyzer
    # outputs are unaffected. Toggle this on only when reverse-engineering
    # SHM layouts or chasing a capture-loop bug.
    telemetry_debug_logs: bool = False

    def to_dict(self) -> dict:
        """Convert config to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        """Create config from dictionary.
        
        Applies schema migrations for legacy field renames and logs warnings
        when unrecognised fields are encountered so they can be cleaned up
        in a future config version.
        """
        data = dict(data)  # shallow copy so we don't mutate the caller's dict

        # --- Step 1: Apply legacy field renames ---
        for old_key, new_key in _LEGACY_FIELD_MAP.items():
            if old_key in data and new_key not in data:
                log_warning(
                    Component.CONFIG,
                    "Migrating legacy config field",
                    old=old_key,
                    new=new_key,
                )
                data[new_key] = data.pop(old_key)

        # --- Step 2: Collect unknown / legacy fields for warning ---
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        legacy_fields = [k for k in data if k not in valid_fields]
        if legacy_fields:
            log_warning(
                Component.CONFIG,
                "Ignoring unknown config field(s) — consider cleaning up old config file",
                fields=legacy_fields,
            )

        # --- Step 3: Filter to only valid fields ---
        filtered = {k: v for k, v in data.items() if k in valid_fields}

        # --- Step 4: Stamp current version so the next load is clean ---
        filtered["config_version"] = CONFIG_VERSION

        return cls(**filtered)


class ConfigManager:
    """
    Manages application configuration.
    
    Handles loading, saving, and updating configuration values.
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Optional custom config file path
        """
        self.config_path = config_path or get_config_path()
        self._config: Optional[AppConfig] = None
        self._loaded = False

    def load(self) -> AppConfig:
        """
        Load configuration from file.
        
        Creates default config if file doesn't exist.
        Migrates old ACE log path to new location.
        
        Returns:
            Loaded or default configuration
        """
        if self._loaded and self._config:
            return self._config

        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config = AppConfig.from_dict(data)
            except (json.JSONDecodeError, IOError) as e:
                log_warning(Component.CONFIG, f"Error loading config: {e}")
                self._config = AppConfig()
        else:
            self._config = AppConfig()

        # Migrate old ACE log path (log.txt → Logs directory)
        old_log_path = str(Path.home() / "Saved Games" / "ACE" / "log.txt")
        if self._config.log_path == old_log_path:
            self._config.log_path = DEFAULT_LOG_PATH
            self.save()

        self._loaded = True
        return self._config

    def save(self) -> bool:
        """
        Save current configuration to file.
        
        Returns:
            True if save was successful
        """
        if not self._config:
            return False

        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            return True
        except IOError as e:
            log_warning(Component.CONFIG, f"Error saving config: {e}")
            return False

    def set(self, config: AppConfig) -> bool:
        """Replace the active configuration and persist it."""
        previous_config = self._config
        previous_loaded = self._loaded
        self._config = config
        self._loaded = True
        if self.save():
            return True

        self._config = previous_config
        self._loaded = previous_loaded
        return False

    def get(self) -> AppConfig:
        """
        Get current configuration.
        
        Returns:
            Current configuration (loads if not already loaded)
        """
        if not self._loaded:
            return self.load()
        return self._config or AppConfig()

    def update(self, **kwargs) -> AppConfig:
        """
        Update configuration values.
        
        Args:
            **kwargs: Configuration values to update
            
        Returns:
            Updated configuration
        """
        config = self.get()

        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)

        self.save()
        return config

    def reset(self) -> AppConfig:
        """
        Reset configuration to defaults.
        
        Returns:
            Default configuration
        """
        self._config = AppConfig()
        self.save()
        return self._config

    def set_discord_config(
        self,
        webhook_url: Optional[str] = None,
        enabled: Optional[bool] = None,
        pb_only: Optional[bool] = None,
        post_invalid: Optional[bool] = None,
    ) -> None:
        """
        Set Discord configuration.
        
        Args:
            webhook_url: Discord webhook URL
            enabled: Whether Discord posting is enabled
            pb_only: Whether to only post personal bests
            post_invalid: Whether to post invalid laps
        """
        updates: dict[str, Any] = {}
        if webhook_url is not None:
            updates["discord_webhook_url"] = webhook_url
        if enabled is not None:
            updates["discord_enabled"] = enabled
        if pb_only is not None:
            updates["discord_pb_only"] = pb_only
        if post_invalid is not None:
            updates["submit_invalid_laps"] = post_invalid

        if updates:
            self.update(**updates)
