"""Focused tests for src.utils.config.ConfigManager."""

import json
from unittest.mock import patch

from src.utils.config import AppConfig, ConfigManager, get_config_path


def test_set_discord_config_post_invalid_maps_to_submit_invalid_laps(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)

    manager.load()
    manager.set_discord_config(post_invalid=True)

    config = manager.get()
    assert config.submit_invalid_laps is True

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["submit_invalid_laps"] is True
    assert "discord_post_invalid" not in saved


def test_load_already_loaded_returns_cached_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)

    config = manager.load()
    config2 = manager.load()

    assert config is config2


def test_load_invalid_json_falls_back_to_default(tmp_path, capsys) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("not valid json", encoding="utf-8")
    manager = ConfigManager(config_path=config_path)

    config = manager.load()

    assert isinstance(config, AppConfig)


def test_load_missing_file_creates_default(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)

    config = manager.load()

    assert isinstance(config, AppConfig)
    assert not config_path.exists()


def test_save_with_no_config_returns_false(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)

    result = manager.save()

    assert result is False


def test_save_io_error_returns_false(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)
    manager.load()

    with patch("pathlib.Path.mkdir", side_effect=OSError("disk full")):
        result = manager.save()

    assert result is False


def test_get_loads_when_not_loaded(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)

    config = manager.get()

    assert isinstance(config, AppConfig)
    assert manager._loaded is True


def test_update_ignores_invalid_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)
    manager.load()

    config = manager.update(theme="light", not_a_real_key="ignored")

    assert config.theme == "light"
    assert not hasattr(config, "not_a_real_key")


def test_reset_reverts_to_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)
    manager.load()
    manager.update(theme="light")

    config = manager.reset()

    assert config.theme == "dark"
    assert config_path.exists()


def test_set_discord_config_no_updates_when_all_none(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)
    manager.load()

    with patch.object(manager, "update") as mock_update:
        manager.set_discord_config()

    mock_update.assert_not_called()


def test_set_discord_config_partial_updates(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    manager = ConfigManager(config_path=config_path)
    manager.load()

    manager.set_discord_config(enabled=True)

    config = manager.get()
    assert config.discord_enabled is True


def test_app_config_from_dict_ignores_invalid_fields() -> None:
    data = {"theme": "light", "legacy_field": "should_be_ignored"}
    config = AppConfig.from_dict(data)

    assert config.theme == "light"
    assert not hasattr(config, "legacy_field")


def test_app_config_to_dict_roundtrip() -> None:
    config = AppConfig(theme="light", server_url="http://test")
    d = config.to_dict()

    assert d["theme"] == "light"
    assert d["server_url"] == "http://test"


def test_config_path_returns_debug_path_when_no_secret() -> None:
    """In debug mode (no APP_SECRET), config path should be config-debug.json."""
    with patch("src.utils.config.is_secret_configured", return_value=False):
        path = get_config_path()
    assert path.name == "config-debug.json"


def test_config_path_returns_production_path_when_secret_configured() -> None:
    """When APP_SECRET is set, config path should be config.json."""
    with patch("src.utils.config.is_secret_configured", return_value=True):
        path = get_config_path()
    assert path.name == "config.json"


def test_default_config_has_current_version() -> None:
    """Default AppConfig should carry the latest CONFIG_VERSION."""
    from src.utils.config import CONFIG_VERSION
    config = AppConfig()
    assert config.config_version == CONFIG_VERSION


def test_from_dict_stamps_config_version_on_old_config() -> None:
    """Loading a dict without config_version should stamp the current version."""
    from src.utils.config import CONFIG_VERSION
    data = {"theme": "light", "auto_submit": False}
    config = AppConfig.from_dict(data)
    assert config.config_version == CONFIG_VERSION
    assert config.theme == "light"
    assert config.auto_submit is False


def test_from_dict_logs_warning_for_unknown_fields(capsys) -> None:
    """Unknown/legacy fields in the loaded dict should trigger a log warning."""
    data = {"theme": "dark", "unknown_field_xyz": "should_warn"}
    AppConfig.from_dict(data)
    captured = capsys.readouterr()
    assert "unknown_field_xyz" in captured.out
    assert "WARNING" in captured.out


def test_from_dict_migrates_legacy_field(capsys) -> None:
    """Legacy field renames via _LEGACY_FIELD_MAP should be applied during load."""
    import src.utils.config as config_mod

    # Temporarily add a legacy mapping to test the migration
    original_map = dict(config_mod._LEGACY_FIELD_MAP)
    try:
        config_mod._LEGACY_FIELD_MAP["old_discord_field"] = "discord_webhook_url"
        data = {"old_discord_field": "https://hook.example.com", "theme": "dark"}
        config = AppConfig.from_dict(data)
        assert config.discord_webhook_url == "https://hook.example.com"
        assert not hasattr(config, "old_discord_field")
        captured = capsys.readouterr()
        assert "Migrating" in captured.out
    finally:
        config_mod._LEGACY_FIELD_MAP.clear()
        config_mod._LEGACY_FIELD_MAP.update(original_map)


def test_to_dict_includes_config_version() -> None:
    """to_dict() output should include config_version."""
    from src.utils.config import CONFIG_VERSION
    config = AppConfig()
    d = config.to_dict()
    assert d["config_version"] == CONFIG_VERSION


def test_from_dict_does_not_mutate_caller_dict() -> None:
    """from_dict should not modify the caller's original dict."""
    original = {"theme": "light", "unknown_legacy": "val"}
    copy_for_call = dict(original)
    AppConfig.from_dict(copy_for_call)
    # Caller's dict should be unmodified
    assert copy_for_call == original


def test_set_replaces_and_persists_config(tmp_path) -> None:
    manager = ConfigManager(config_path=tmp_path / "config.json")
    manager.load()
    replacement = AppConfig(telemetry_enabled=True)

    assert manager.set(replacement) is True
    assert manager.get() is replacement
    assert ConfigManager(config_path=manager.config_path).load().telemetry_enabled is True


def test_set_rolls_back_in_memory_config_when_save_fails(tmp_path) -> None:
    manager = ConfigManager(config_path=tmp_path / "config.json")
    original = manager.load()
    replacement = AppConfig(telemetry_enabled=True)

    with patch.object(manager, "save", return_value=False):
        assert manager.set(replacement) is False

    assert manager.get() is original
