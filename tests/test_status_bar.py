"""Tests for status_bar UI component."""

from unittest.mock import MagicMock, patch

from src.ui.components.status_bar import StatusBar, ConnectionStatus


def test_init_default_status():
    with patch("src.ui.components.status_bar.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.Container.return_value = MagicMock()
        bar = StatusBar()

    assert bar._connection_status == ConnectionStatus.DISCONNECTED
    assert bar._status_message == "Not connected"


def test_get_status_color_for_each_state():
    with patch("src.ui.components.status_bar.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.Container.return_value = MagicMock()
        bar = StatusBar()

    assert bar._get_status_color() == "#888888"
    bar._connection_status = ConnectionStatus.CONNECTING
    assert bar._get_status_color() == "#ffd43b"
    bar._connection_status = ConnectionStatus.CONNECTED
    assert bar._get_status_color() == "#51cf66"
    bar._connection_status = ConnectionStatus.ERROR
    assert bar._get_status_color() == "#ff6b6b"


def test_get_status_icon_connecting_returns_progress_ring():
    with patch("src.ui.components.status_bar.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.Container.return_value = MagicMock()
        bar = StatusBar()
        bar._connection_status = ConnectionStatus.CONNECTING

        bar._get_status_icon()
        mock_ft.ProgressRing.assert_called_once()


def test_get_status_icon_non_connecting_returns_container():
    with patch("src.ui.components.status_bar.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.Container.return_value = MagicMock()
        bar = StatusBar()

        bar._get_status_icon()
        assert mock_ft.Container.called


def test_set_status_updates_state():
    with patch("src.ui.components.status_bar.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.Container.return_value = MagicMock()
        bar = StatusBar()

        with patch.object(bar, "update"):
            bar.set_status(connection_status=ConnectionStatus.CONNECTED, message="Online")

    assert bar._connection_status == ConnectionStatus.CONNECTED
    assert bar._status_message == "Online"


def test_set_status_with_none_leaves_existing_values():
    with patch("src.ui.components.status_bar.ft") as mock_ft:
        mock_ft.Row.return_value = MagicMock()
        mock_ft.Text.return_value = MagicMock()
        mock_ft.Container.return_value = MagicMock()
        bar = StatusBar()

        with patch.object(bar, "update"):
            bar.set_status()

    assert bar._connection_status == ConnectionStatus.DISCONNECTED
    assert bar._status_message == "Not connected"
