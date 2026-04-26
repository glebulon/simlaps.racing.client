"""
Configuration Manager for SimLaps Client.

Handles persistent settings stored in AppData.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


# Default configuration values
DEFAULT_LOG_PATH = str(Path.home() / "Saved Games" / "ACE" / "Logs")
DEFAULT_SERVER_URL = "https://simlaps.racing"
APP_NAME = "SimLapsClient"


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
    """Get the configuration file path."""
    return get_config_dir() / "config.json"


@dataclass
class AppConfig:
    """Application configuration settings."""
    
    # Authentication
    steam_id: Optional[str] = None
    steam_name: Optional[str] = None
    api_key: Optional[str] = None
    
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
        """Create config from dictionary."""
        # Filter to only valid fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)
    
    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return bool(self.steam_id and self.api_key)


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
                print(f"Error loading config: {e}")
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
            print(f"Error saving config: {e}")
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
    
    def clear_auth(self) -> None:
        """Clear authentication data."""
        config = self.get()
        config.steam_id = None
        config.steam_name = None
        config.api_key = None
        self.save()
    
    def set_auth(
        self,
        steam_id: str,
        api_key: str,
        steam_name: Optional[str] = None,
    ) -> None:
        """
        Set authentication data.
        
        Args:
            steam_id: Steam ID64
            api_key: API key for server
            steam_name: Optional Steam display name
        """
        config = self.get()
        config.steam_id = steam_id
        config.api_key = api_key
        config.steam_name = steam_name
        self.save()
    
    def get_log_path(self) -> Path:
        """Get the log file path as Path object."""
        return Path(self.get().log_path)
    
    def set_log_path(self, path: str) -> None:
        """Set the log file path."""
        self.update(log_path=path)
    
    def get_server_url(self) -> str:
        """Get the server URL."""
        return self.get().server_url
    
    def set_server_url(self, url: str) -> None:
        """Set the server URL."""
        self.update(server_url=url.rstrip("/"))
    
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
        updates = {}
        if webhook_url is not None:
            updates["discord_webhook_url"] = webhook_url
        if enabled is not None:
            updates["discord_enabled"] = enabled
        if pb_only is not None:
            updates["discord_pb_only"] = pb_only
        if post_invalid is not None:
            updates["discord_post_invalid"] = post_invalid
        
        if updates:
            self.update(**updates)
    
    def is_discord_configured(self) -> bool:
        """Check if Discord is properly configured."""
        config = self.get()
        return bool(config.discord_webhook_url and config.discord_enabled)


# Global config manager instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get the global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_config() -> AppConfig:
    """Get the current configuration."""
    return get_config_manager().get()


def save_config() -> bool:
    """Save the current configuration."""
    return get_config_manager().save()
