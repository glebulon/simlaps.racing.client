"""Focused tests for src.utils.config.ConfigManager."""

import json
from unittest.mock import patch

import pytest

from src.utils.config import ConfigManager, AppConfig, get_config_manager, get_config_path


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


def test_get_config_manager_returns_singleton() -> None:
    from src.utils.config import _config_manager

    # Reset global to test fresh
    import src.utils.config as config_mod
    orig = config_mod._config_manager
    config_mod._config_manager = None

    try:
        m1 = get_config_manager()
        m2 = get_config_manager()
        assert m1 is m2
    finally:
        config_mod._config_manager = orig


def test_config_path_returns_path() -> None:
    path = get_config_path()
    assert path.name == "config.json"
