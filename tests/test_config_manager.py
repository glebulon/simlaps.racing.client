"""Focused tests for src.utils.config.ConfigManager."""

import json

from src.utils.config import ConfigManager


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
