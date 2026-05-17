from types import SimpleNamespace
from unittest.mock import MagicMock

from src.ui.services.settings_service import SettingsService
from src.utils.config import AppConfig


def _make_app() -> SimpleNamespace:
    app = SimpleNamespace()
    app.page = MagicMock()
    app._config = AppConfig(server_url="https://simlaps.racing", telemetry_enabled=False)
    app._config_manager = MagicMock()
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


def test_apply_disabling_telemetry_stops_capture_and_clears_ui():
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.is_capturing.return_value = True
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

    app.page.run_task.assert_called_once_with(telemetry_capture.stop_capture, "disabled")
    app._home_page.set_telemetry_button.assert_called_once_with(None, "")
    assert app._telemetry_capture is None
    assert app._telemetry_analyzer is None
    assert app._telemetry_button is None


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
