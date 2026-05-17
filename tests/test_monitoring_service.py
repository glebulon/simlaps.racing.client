import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_stop_with_none_tasks_does_not_crash():
    page = MagicMock()
    service = MonitoringService(page)
    service._parser_task = None
    service._game_monitor_task = None

    log_parser = MagicMock()
    home_page = MagicMock()

    service.stop(log_parser=log_parser, home_page=home_page)

    log_parser.stop.assert_called_once()
    home_page.set_connection_status.assert_called_once_with(
        ConnectionStatus.DISCONNECTED,
        "Monitoring stopped",
    )


@pytest.mark.asyncio
async def test_run_game_monitor_triggers_stop_when_game_gone():
    page = MagicMock()
    service = MonitoringService(page)

    on_change = AsyncMock()
    calls = []

    def is_capturing():
        calls.append(1)
        return len(calls) >= 2  # Start capturing on second poll

    with patch("src.ui.services.monitoring_service.is_game_running", return_value=False):
        with patch("asyncio.sleep", new=AsyncMock()):
            task = asyncio.create_task(
                service._run_game_monitor(on_change, is_capturing)
            )
            await asyncio.wait_for(task, timeout=2.0)

    on_change.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_run_game_monitor_cancelled_gracefully():
    page = MagicMock()
    service = MonitoringService(page)

    on_change = AsyncMock()

    task = asyncio.create_task(
        service._run_game_monitor(on_change, lambda: False)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.sleep(0.1)

    assert task.done()


@pytest.mark.asyncio
async def test_run_parser_exception_updates_status():
    page = MagicMock()
    service = MonitoringService(page)

    log_parser = MagicMock()
    log_parser.follow = AsyncMock(side_effect=RuntimeError("parser boom"))
    home_page = MagicMock()

    await service._run_parser(log_parser, home_page)

    home_page.set_connection_status.assert_called_once_with(
        ConnectionStatus.ERROR,
        "Error: parser boom",
    )


@pytest.mark.asyncio
async def test_run_parser_cancelled_gracefully():
    page = MagicMock()
    service = MonitoringService(page)

    log_parser = MagicMock()

    async def slow_follow():
        await asyncio.sleep(10)

    log_parser.follow = slow_follow
    home_page = MagicMock()

    task = asyncio.create_task(service._run_parser(log_parser, home_page))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.1)

    assert task.done()


def test_get_game_version_from_log_found(tmp_path):
    log_file = tmp_path / "log.txt"
    log_file.write_text(
        "[2026-01-01] [core] Build release 0.9.3, branch main\n",
        encoding="utf-8",
    )

    result = MonitoringService._get_game_version_from_log(str(log_file))
    assert result == "0.9.3"


def test_get_game_version_from_log_not_found(tmp_path):
    log_file = tmp_path / "log.txt"
    log_file.write_text("some unrelated content\n", encoding="utf-8")

    result = MonitoringService._get_game_version_from_log(str(log_file))
    assert result is None


def test_get_game_version_from_missing_file():
    result = MonitoringService._get_game_version_from_log("/does/not/exist.log")
    assert result is None


def test_get_game_version_from_log_exception_handled(tmp_path):
    log_file = tmp_path / "log.txt"
    log_file.write_text("content", encoding="utf-8")

    with patch("os.path.exists", side_effect=OSError("bad path")):
        result = MonitoringService._get_game_version_from_log(str(log_file))

    assert result is None
