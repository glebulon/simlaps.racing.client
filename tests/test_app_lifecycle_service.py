from types import SimpleNamespace
from unittest.mock import MagicMock

from src.ui.services.app_lifecycle_service import AppLifecycleService


def _make_app() -> SimpleNamespace:
    app = SimpleNamespace()
    app.page = MagicMock()
    app._telemetry_capture = None
    app._api_client = None
    app.stop_monitoring = MagicMock()
    return app


def test_cleanup_stops_active_telemetry_monitoring_and_api_client():
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.is_capturing.return_value = True
    app._telemetry_capture = telemetry_capture
    app._api_client = MagicMock()

    service = AppLifecycleService()
    service.cleanup(app=app)

    app.page.run_task.assert_any_call(telemetry_capture.stop_capture, "app_close")
    app.page.run_task.assert_any_call(app._api_client.close)
    app.stop_monitoring.assert_called_once_with()


def test_cleanup_skips_telemetry_stop_when_not_capturing():
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.is_capturing.return_value = False
    app._telemetry_capture = telemetry_capture

    service = AppLifecycleService()
    service.cleanup(app=app)

    app.page.run_task.assert_not_called()
    app.stop_monitoring.assert_called_once_with()


def test_cleanup_is_idempotent():
    app = _make_app()
    telemetry_capture = MagicMock()
    telemetry_capture.is_capturing.return_value = True
    app._telemetry_capture = telemetry_capture
    app._api_client = MagicMock()

    service = AppLifecycleService()
    service.cleanup(app=app)
    service.cleanup(app=app)

    app.stop_monitoring.assert_called_once_with()
    assert app.page.run_task.call_count == 2
