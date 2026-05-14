from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ui.components.status_bar import ConnectionStatus
from src.ui.services.monitoring_service import MonitoringService


class _DummyTask:
    def __init__(self):
        self._cancelled = False

    def done(self):
        return False

    def cancel(self):
        self._cancelled = True


@pytest.mark.asyncio
async def test_start_sets_status_and_starts_background_tasks(tmp_path):
    log_file = tmp_path / "log.txt"
    log_file.write_text(
        "[2026-01-01] [core] Build release 0.8.9, branch main\n",
        encoding="utf-8",
    )

    page = MagicMock()
    page.run_task.side_effect = [_DummyTask(), _DummyTask()]

    service = MonitoringService(page)
    log_parser = MagicMock()
    home_page = MagicMock()

    on_game_status_change = AsyncMock()

    await service.start(
        log_parser=log_parser,
        home_page=home_page,
        log_path=str(log_file),
        on_game_status_change=on_game_status_change,
        is_telemetry_capturing=lambda: False,
    )

    home_page.set_game_version.assert_called_once_with("0.8.9")
    home_page.set_game_running.assert_called_once_with(False)
    home_page.set_connection_status.assert_called_once_with(
        ConnectionStatus.CONNECTED,
        "Monitoring log file...",
    )
    assert page.run_task.call_count == 2


@pytest.mark.asyncio
async def test_start_is_noop_when_already_active(tmp_path):
    page = MagicMock()
    page.run_task.side_effect = [_DummyTask(), _DummyTask()]

    service = MonitoringService(page)
    log_parser = MagicMock()
    home_page = MagicMock()
    on_game_status_change = AsyncMock()

    await service.start(
        log_parser=log_parser,
        home_page=home_page,
        log_path=str(tmp_path / "missing.log"),
        on_game_status_change=on_game_status_change,
        is_telemetry_capturing=lambda: False,
    )

    await service.start(
        log_parser=log_parser,
        home_page=home_page,
        log_path=str(tmp_path / "missing.log"),
        on_game_status_change=on_game_status_change,
        is_telemetry_capturing=lambda: False,
    )

    assert page.run_task.call_count == 2


def test_stop_cancels_tasks_and_updates_status():
    page = MagicMock()
    service = MonitoringService(page)

    parser_task = _DummyTask()
    monitor_task = _DummyTask()
    service._parser_task = parser_task
    service._game_monitor_task = monitor_task

    log_parser = MagicMock()
    home_page = MagicMock()

    service.stop(log_parser=log_parser, home_page=home_page)

    log_parser.stop.assert_called_once()
    assert parser_task._cancelled is True
    assert monitor_task._cancelled is True
    home_page.set_connection_status.assert_called_once_with(
        ConnectionStatus.DISCONNECTED,
        "Monitoring stopped",
    )
