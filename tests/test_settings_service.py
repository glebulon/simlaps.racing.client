from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.ui.services.settings_service import SettingsService
from src.ui.components.telemetry_status import TelemetryButton
from src.utils.config import AppConfig


def _make_app() -> SimpleNamespace:
    app = SimpleNamespace()
    app.page = MagicMock()
    app._config = AppConfig(server_url="https://simlaps.racing", telemetry_enabled=False)
    app._config_manager = MagicMock()
    app._config_manager.set.return_value = True
    app._discord_notifier = None
    app._pb_cache = MagicMock()
    app._pb_cache.server_url = "https://simlaps.racing"
    app._session_manager = MagicMock()
    app._telemetry_capture = None
    app._telemetry_analyzer = None
    app._telemetry_button = None
    app._home_page = MagicMock()
    app._log_parser = MagicMock()
    app._log_parser.is_running = False
    app._start_telemetry_capture = MagicMock()
    app.start_monitoring = MagicMock()
    app.stop_monitoring = MagicMock()
    app._init_telemetry_services = MagicMock()
    app._attach_telemetry_ui = MagicMock()
    return app


def test_apply_enabling_telemetry_schedules_capture_start():
    app = _make_app()

    def init_telemetry_services() -> None:
        app._telemetry_capture = MagicMock()

    app._init_telemetry_services.side_effect = init_telemetry_services

    service = SettingsService()
    config = AppConfig(server_url="https://simlaps.racing", telemetry_enabled=True)

    service.apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(),
        get_pb_cache_for_server=MagicMock(),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    app._init_telemetry_services.assert_called_once()
    app._attach_telemetry_ui.assert_called_once()
    app.page.run_task.assert_called_once_with(app._start_telemetry_capture)


def test_apply_disabling_telemetry_switches_to_validity_only_mode():
    """When telemetry is disabled the capture loop stays alive for SHM
    validity but stops recording frames.  The analyzer and UI button are
    torn down since there are no frames to analyze."""
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.is_capturing.return_value = True
    telemetry_capture.record_frames = True
    app._telemetry_capture = telemetry_capture
    app._telemetry_analyzer = MagicMock()
    app._telemetry_button = MagicMock()

    service = SettingsService()
    config = AppConfig(server_url="https://simlaps.racing", telemetry_enabled=False)

    service.apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(),
        get_pb_cache_for_server=MagicMock(),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    # Capture loop stays alive — only recording mode changes.
    telemetry_capture.set_record_frames.assert_called_once_with(False)
    app._home_page.set_telemetry_button.assert_called_once_with(None, "")
    # The capture instance itself is NOT destroyed.
    assert app._telemetry_capture is telemetry_capture
    assert app._telemetry_analyzer is None
    assert app._telemetry_button is None


def test_apply_reconfigures_validity_only_capture():
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.record_frames = False
    app._telemetry_capture = telemetry_capture
    app._config.telemetry_output_path = "C:/old"

    config = AppConfig(
        server_url="https://simlaps.racing",
        telemetry_enabled=False,
        telemetry_output_path="C:/new",
        telemetry_debug_logs=True,
    )
    SettingsService().apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(),
        get_pb_cache_for_server=MagicMock(),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    telemetry_capture.configure.assert_called_once_with(
        output_dir="C:/new",
        debug_logs=True,
    )


def test_apply_enables_existing_validity_only_capture_without_import_error():
    """Exercise the exact Settings checkbox branch used by the desktop UI."""
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.record_frames = False
    telemetry_capture.is_capturing.return_value = True
    app._telemetry_capture = telemetry_capture
    app._telemetry_analyzer = None
    app._telemetry_button = None
    app._open_telemetry_location = MagicMock()

    config = AppConfig(server_url="https://simlaps.racing", telemetry_enabled=True)
    SettingsService().apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(),
        get_pb_cache_for_server=MagicMock(),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    telemetry_capture.set_record_frames.assert_called_once_with(True)
    assert app._telemetry_analyzer is not None
    assert isinstance(app._telemetry_button, TelemetryButton)
    app._attach_telemetry_ui.assert_called_once_with()
    app._config_manager.set.assert_called_once_with(config)


def test_apply_restarts_monitoring_when_parser_was_running():
    app = _make_app()
    app._log_parser.is_running = True
    old_parser = app._log_parser

    new_parser = MagicMock()
    create_log_parser = MagicMock(return_value=new_parser)

    service = SettingsService()
    config = AppConfig(server_url="https://simlaps.racing", log_path="C:/logs")

    service.apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(),
        get_pb_cache_for_server=MagicMock(),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=create_log_parser,
    )

    app.stop_monitoring.assert_called_once_with()
    create_log_parser.assert_called_once_with("C:/logs")
    app.page.run_task.assert_called_once_with(app.start_monitoring)
    assert app._log_parser is not old_parser
    app._home_page.update_config.assert_called_once_with(config)


def test_apply_creates_discord_notifier_when_configured():
    app = _make_app()
    notifier = MagicMock()
    create_discord_notifier = MagicMock(return_value=notifier)

    service = SettingsService()
    config = AppConfig(
        server_url="https://simlaps.racing",
        discord_enabled=True,
        discord_webhook_url="https://discord.com/api/webhooks/123/abc",
    )

    service.apply(
        app=app,
        config=config,
        create_discord_notifier=create_discord_notifier,
        get_pb_cache_for_server=MagicMock(),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    create_discord_notifier.assert_called_once_with("https://discord.com/api/webhooks/123/abc")
    assert app._discord_notifier is notifier


@pytest.mark.parametrize(
    "webhook_url",
    [
        "malformed",
        "http://discord.com/api/webhooks/123/token",
        "https://127.0.0.1/api/webhooks/123/token",
        "https://user:password@discord.com/api/webhooks/123/token",
    ],
)
def test_apply_rejects_invalid_enabled_webhook_before_persistence(webhook_url):
    app = _make_app()
    create_notifier = MagicMock()
    config = AppConfig(
        server_url="https://simlaps.racing",
        discord_enabled=True,
        discord_webhook_url=webhook_url,
    )

    with pytest.raises(ValueError, match="Invalid Discord webhook URL"):
        SettingsService().apply(
            app=app,
            config=config,
            create_discord_notifier=create_notifier,
            get_pb_cache_for_server=MagicMock(),
            create_api_client=MagicMock(),
            create_log_parser=MagicMock(),
        )

    app._config_manager.set.assert_not_called()
    create_notifier.assert_not_called()


def test_apply_updates_pb_cache_when_server_url_changes():
    app = _make_app()
    app._pb_cache.server_url = "https://old-server.com"
    new_cache = MagicMock()
    get_pb_cache_for_server = MagicMock(return_value=new_cache)

    service = SettingsService()
    config = AppConfig(server_url="https://new-server.com")

    service.apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(),
        get_pb_cache_for_server=get_pb_cache_for_server,
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    get_pb_cache_for_server.assert_called_once_with("https://new-server.com")
    assert app._pb_cache is new_cache


def test_apply_persists_while_all_live_state_still_uses_previous_config():
    app = _make_app()
    previous = app._config
    old_discord = MagicMock()
    old_pb_cache = app._pb_cache
    old_api_client = MagicMock()
    old_parser = app._log_parser
    old_analyzer = MagicMock()
    old_button = MagicMock()
    capture = MagicMock(record_frames=False)
    app._discord_notifier = old_discord
    app._api_client = old_api_client
    app._telemetry_capture = capture
    app._telemetry_analyzer = old_analyzer
    app._telemetry_button = old_button

    config = AppConfig(
        server_url="https://new-server.com",
        log_path="C:/new-logs",
        discord_enabled=True,
        discord_webhook_url="https://discord.com/api/webhooks/123/new",
        telemetry_enabled=True,
        telemetry_output_path="C:/new-telemetry",
    )

    def assert_unpublished(persisted):
        assert persisted is config
        assert app._config is previous
        assert app._discord_notifier is old_discord
        assert app._pb_cache is old_pb_cache
        assert app._api_client is old_api_client
        assert app._log_parser is old_parser
        assert app._telemetry_analyzer is old_analyzer
        assert app._telemetry_button is old_button
        capture.set_record_frames.assert_not_called()
        capture.configure.assert_not_called()
        app.stop_monitoring.assert_not_called()
        app._home_page.update_config.assert_not_called()
        return True

    app._config_manager.set.side_effect = assert_unpublished

    SettingsService().apply(
        app=app,
        config=config,
        create_discord_notifier=MagicMock(return_value=MagicMock()),
        get_pb_cache_for_server=MagicMock(return_value=MagicMock()),
        create_api_client=MagicMock(return_value=MagicMock()),
        create_log_parser=MagicMock(return_value=MagicMock()),
    )

    assert app._config is config


@pytest.mark.parametrize(
    "changes",
    [
        {"server_url": "https://new-server.com"},
        {"log_path": "C:/new-logs"},
        {
            "discord_enabled": True,
            "discord_webhook_url": "https://discord.com/api/webhooks/123/new",
        },
        {
            "telemetry_enabled": True,
            "telemetry_output_path": "C:/new-telemetry",
            "telemetry_debug_logs": True,
        },
    ],
    ids=["server", "log", "discord", "telemetry"],
)
def test_apply_persistence_failure_does_not_mutate_live_state(changes):
    app = _make_app()
    previous = app._config
    old_discord = MagicMock()
    old_pb_cache = app._pb_cache
    old_api_client = MagicMock()
    old_parser = app._log_parser
    old_analyzer = MagicMock()
    old_button = MagicMock()
    capture = MagicMock(record_frames=False)
    app._discord_notifier = old_discord
    app._api_client = old_api_client
    app._telemetry_capture = capture
    app._telemetry_analyzer = old_analyzer
    app._telemetry_button = old_button
    app._config_manager.set.return_value = False
    config_values = previous.to_dict()
    config_values.update(changes)
    config = AppConfig.from_dict(config_values)

    with pytest.raises(OSError, match="Could not save application settings"):
        SettingsService().apply(
            app=app,
            config=config,
            create_discord_notifier=MagicMock(return_value=MagicMock()),
            get_pb_cache_for_server=MagicMock(return_value=MagicMock()),
            create_api_client=MagicMock(return_value=MagicMock()),
            create_log_parser=MagicMock(return_value=MagicMock()),
        )

    assert app._config is previous
    assert app._discord_notifier is old_discord
    assert app._pb_cache is old_pb_cache
    assert app._api_client is old_api_client
    assert app._log_parser is old_parser
    assert app._telemetry_capture is capture
    assert app._telemetry_analyzer is old_analyzer
    assert app._telemetry_button is old_button
    capture.set_record_frames.assert_not_called()
    capture.configure.assert_not_called()
    app.stop_monitoring.assert_not_called()
    app.start_monitoring.assert_not_called()
    app._init_telemetry_services.assert_not_called()
    app._attach_telemetry_ui.assert_not_called()
    app._home_page.update_config.assert_not_called()
    app.page.run_task.assert_not_called()
