import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ui.app import SimLapsApp
from src.ui.components.status_bar import ConnectionStatus
from src.ui.services.session_lifecycle_service import SessionLifecycleService


def _make_service(*, capturing: bool = False, home_page=...):
    home = MagicMock() if home_page is ... else home_page
    manager = MagicMock()
    manager.get_best_lap_time.side_effect = [90000, None]
    manager.get_all_lap_times.side_effect = [[90000], []]
    capture = MagicMock()
    capture.is_capturing.return_value = capturing
    start = AsyncMock()
    stop = AsyncMock()
    service = SessionLifecycleService(
        home_page=home,
        session_manager=manager,
        telemetry_capture=capture,
        start_capture=start,
        stop_capture=stop,
    )
    return service, home, manager, capture, start, stop


@pytest.mark.asyncio
@pytest.mark.parametrize("capturing", [False, True])
async def test_car_removed_only_stops_an_active_capture(capturing):
    service, _, _, _, start, stop = _make_service(capturing=capturing)

    with patch(
        "src.ui.services.session_lifecycle_service.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        await service.handle_car_removed()

    start.assert_not_awaited()
    if capturing:
        sleep.assert_awaited_once_with(1.0)
        stop.assert_awaited_once_with("car_removed")
    else:
        sleep.assert_not_awaited()
        stop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("capturing", [False, True])
async def test_restart_resets_and_always_starts_fresh_capture(capturing):
    service, _, manager, _, start, stop = _make_service(capturing=capturing)

    await service.handle_session_restart()

    manager.reset.assert_called_once_with()
    start.assert_awaited_once_with()
    if capturing:
        stop.assert_awaited_once_with("session_restart", discard=True)
    else:
        stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_game_running_resets_session_updates_ui_and_starts_capture():
    service, home, manager, _, start, stop = _make_service(capturing=False)

    await service.handle_game_status_change(True)

    home.set_game_running.assert_called_once_with(True)
    home.set_connection_status.assert_called_once_with(
        ConnectionStatus.CONNECTED,
        "Session active - recording laps",
    )
    manager.reset.assert_called_once_with()
    start.assert_awaited_once_with()
    stop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("capturing", [False, True])
async def test_game_stopped_preserves_delay_and_stop_reason(capturing):
    service, home, manager, _, start, stop = _make_service(capturing=capturing)

    with patch(
        "src.ui.services.session_lifecycle_service.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        await service.handle_game_status_change(False)

    home.set_game_running.assert_called_once_with(False)
    home.set_connection_status.assert_called_once_with(
        ConnectionStatus.CONNECTED,
        "Monitoring - waiting for session...",
    )
    manager.reset.assert_not_called()
    start.assert_not_awaited()
    stop.assert_awaited_once_with("session_end")
    if capturing:
        sleep.assert_awaited_once_with(2.0)
    else:
        sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_delayed_game_stop_cannot_stop_capture_started_by_new_game_status():
    """A reordered game-status callback must leave the new run capturing."""
    service, _, _, capture, start, stop = _make_service(capturing=True)
    delay_started = asyncio.Event()
    release_delay = asyncio.Event()

    async def blocked_sleep(seconds):
        assert seconds == 2.0
        delay_started.set()
        await release_delay.wait()

    with patch(
        "src.ui.services.session_lifecycle_service.asyncio.sleep",
        side_effect=blocked_sleep,
    ):
        stale_stop = asyncio.create_task(service.handle_game_status_change(False))
        await delay_started.wait()

        await service.handle_game_status_change(True)
        release_delay.set()
        await stale_stop

    stop.assert_not_awaited()
    start.assert_awaited_once_with()
    assert capture.is_capturing()


@pytest.mark.asyncio
async def test_delayed_car_removed_stop_cannot_stop_capture_started_by_restart():
    """A reordered car-removal callback must leave the restarted run capturing."""
    service, _, _, capture, start, stop = _make_service(capturing=True)
    delay_started = asyncio.Event()
    release_delay = asyncio.Event()

    async def blocked_sleep(seconds):
        assert seconds == 1.0
        delay_started.set()
        await release_delay.wait()

    with patch(
        "src.ui.services.session_lifecycle_service.asyncio.sleep",
        side_effect=blocked_sleep,
    ):
        stale_stop = asyncio.create_task(service.handle_car_removed())
        await delay_started.wait()

        await service.handle_session_restart()
        release_delay.set()
        await stale_stop

    stop.assert_awaited_once_with("session_restart", discard=True)
    start.assert_awaited_once_with()
    assert capture.is_capturing()


@pytest.mark.asyncio
async def test_game_status_is_noop_without_home_page():
    service, _, manager, capture, start, stop = _make_service(
        capturing=True,
        home_page=None,
    )

    await service.handle_game_status_change(True)
    await service.handle_game_status_change(False)

    manager.reset.assert_not_called()
    capture.is_capturing.assert_not_called()
    start.assert_not_awaited()
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_simlaps_app_parser_callbacks_reach_session_lifecycle_service():
    """Exercise the real parser callback methods across the app/service boundary."""
    app = SimLapsApp.__new__(SimLapsApp)
    service, home, manager, capture, start, stop = _make_service(capturing=True)
    app._session_lifecycle_service = service

    with patch(
        "src.ui.services.session_lifecycle_service.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await app._on_car_removed()
        await app._on_session_restart()
        capture.is_capturing.return_value = False
        await app._on_game_status_change(True)

    assert stop.await_args_list[0].args == ("car_removed",)
    assert stop.await_args_list[1].args == ("session_restart",)
    assert stop.await_args_list[1].kwargs == {"discard": True}
    assert manager.reset.call_count == 2
    assert start.await_count == 2
    home.set_game_running.assert_called_once_with(True)
