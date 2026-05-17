"""Tests for telemetry_status UI component."""

from unittest.mock import MagicMock, patch

from src.ui.components.telemetry_status import (
    TelemetryStatusIndicator,
    TelemetryButton,
    TelemetryStatus,
)


def test_init_default_state():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

    assert indicator._status == TelemetryStatus.IDLE
    assert indicator.visible is False


def test_set_status_idle():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

        with patch.object(indicator, "update"):
            indicator.set_status(TelemetryStatus.IDLE)

        assert indicator.visible is False


def test_set_status_capturing():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

        with patch.object(indicator, "update"):
            indicator.set_status(TelemetryStatus.CAPTURING, frame_count=42)

        assert indicator._status == TelemetryStatus.CAPTURING
        assert indicator._frame_count == 42
        assert indicator.visible is True


def test_set_status_analyzing():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

        with patch.object(indicator, "update"):
            indicator.set_status(TelemetryStatus.ANALYZING)

        assert indicator._status == TelemetryStatus.ANALYZING
        assert indicator.visible is True


def test_set_status_complete():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

        with patch.object(indicator, "update"):
            indicator.set_status(TelemetryStatus.COMPLETE, result_path="/tmp/out.html")

        assert indicator._status == TelemetryStatus.COMPLETE
        assert indicator._last_result_path == "/tmp/out.html"
        assert indicator.visible is True


def test_set_status_error():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

        with patch.object(indicator, "update"):
            indicator.set_status(TelemetryStatus.ERROR)

        assert indicator._status == TelemetryStatus.ERROR
        assert indicator.visible is True


def test_show_and_hide():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        indicator = TelemetryStatusIndicator()

        with patch.object(indicator, "update"):
            indicator.show()
            assert indicator.visible is True
            indicator.hide()
            assert indicator.visible is False


def test_telemetry_button_handle_click():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.ElevatedButton.return_value = MagicMock()
        callback = MagicMock()
        button = TelemetryButton(on_click=callback, output_path="/tmp/telemetry")

        event = MagicMock()
        button._handle_click(event)
        callback.assert_called_once_with(event, "/tmp/telemetry")


def test_telemetry_button_handle_click_no_callback():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.ElevatedButton.return_value = MagicMock()
        button = TelemetryButton(on_click=None)

        event = MagicMock()
        button._handle_click(event)  # should not crash


def test_telemetry_button_update_path():
    with patch("src.ui.components.telemetry_status.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Icon.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.ElevatedButton.return_value = MagicMock()
        button = TelemetryButton(output_path="/old")

        button.update_path("/new")
        assert button.output_path == "/new"
