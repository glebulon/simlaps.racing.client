import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest
from flet.messaging.connection import Connection
from flet.messaging.session import Session
from flet.pubsub.pubsub_hub import PubSubHub

from src.ui.app import SimLapsApp
from src.ui.services.app_lifecycle_service import AppLifecycleService
from src.utils.config import AppConfig


def _make_app(order=None) -> SimpleNamespace:
    order = order if order is not None else []
    app = SimpleNamespace()
    app.page = SimpleNamespace(
        window=SimpleNamespace(
            destroy=AsyncMock(side_effect=lambda: order.append("destroy")),
        )
    )
    app._stop_telemetry_capture = AsyncMock(side_effect=lambda **kwargs: order.append("telemetry"))
    app._api_client = SimpleNamespace(
        close=AsyncMock(side_effect=lambda: order.append("api")),
    )
    app.stop_monitoring = MagicMock(side_effect=lambda: order.append("monitor"))
    return app


@pytest.mark.asyncio
async def test_cleanup_stops_monitor_finalizes_telemetry_closes_api_before_destroy():
    order = []
    app = _make_app(order)

    await AppLifecycleService().cleanup(app=app)

    assert order == ["monitor", "telemetry", "api", "destroy"]
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")
    app._api_client.close.assert_awaited_once_with()
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_for_repeated_events():
    app = _make_app()
    service = AppLifecycleService()

    await service.cleanup(app=app)
    await service.cleanup(app=app)

    app.stop_monitoring.assert_called_once_with()
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")
    app._api_client.close.assert_awaited_once_with()
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cleanup_logs_failure_and_continues_remaining_steps():
    order = []
    app = _make_app(order)
    app.stop_monitoring.side_effect = RuntimeError("monitor failed")
    app._stop_telemetry_capture.side_effect = RuntimeError("telemetry failed")
    app._api_client.close.side_effect = RuntimeError("api failed")

    with patch("src.ui.services.app_lifecycle_service.log_exception") as log_error:
        await AppLifecycleService().cleanup(app=app)

    assert order == ["destroy"]
    assert log_error.call_count == 3
    assert "monitor shutdown" in log_error.call_args_list[0].args[1]
    assert "telemetry shutdown" in log_error.call_args_list[1].args[1]
    assert "API client close" in log_error.call_args_list[2].args[1]
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_native_close_event_uses_async_cleanup():
    app = SimLapsApp.__new__(SimLapsApp)
    app._app_lifecycle_service = MagicMock()
    app._app_lifecycle_service.cleanup = AsyncMock()

    await app._on_window_event(
        ft.WindowEvent(
            name="window",
            control=MagicMock(),
            type=ft.WindowEventType.CLOSE,
        )
    )
    await app._on_window_event(
        ft.WindowEvent(
            name="window",
            control=MagicMock(),
            type=ft.WindowEventType.FOCUS,
        )
    )

    app._app_lifecycle_service.cleanup.assert_awaited_once_with(app=app)


@pytest.mark.asyncio
async def test_close_callbacks_share_cleanup_when_first_callback_is_cancelled():
    """A cancelled native callback must not cancel the real cleanup task."""
    order = []
    monitor_started = asyncio.Event()
    release_monitor = asyncio.Event()

    async def stop_monitoring():
        order.append("monitor")
        monitor_started.set()
        await release_monitor.wait()

    app = _make_app(order)
    app.stop_monitoring = stop_monitoring
    service = AppLifecycleService()

    # Exercise the actual SimLapsApp lifecycle callbacks, as Flet invokes them.
    app_controller = SimLapsApp.__new__(SimLapsApp)
    app_controller._app_lifecycle_service = service
    app_controller.page = app.page
    app_controller._stop_telemetry_capture = app._stop_telemetry_capture
    app_controller._api_client = app._api_client
    app_controller.stop_monitoring = app.stop_monitoring

    close_event = ft.WindowEvent(
        name="window",
        control=MagicMock(),
        type=ft.WindowEventType.CLOSE,
    )
    first = asyncio.create_task(SimLapsApp._on_window_event(app_controller, close_event))
    await monitor_started.wait()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    # The fallback callback arrives while native cleanup is still blocked and
    # must await the same task rather than returning early.
    second = asyncio.create_task(SimLapsApp._on_page_disconnect(app_controller))
    await asyncio.sleep(0)
    assert order == ["monitor"]
    assert not second.done()

    release_monitor.set()
    await second

    assert order == ["monitor", "telemetry", "api", "destroy"]
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")
    app._api_client.close.assert_awaited_once_with()
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_concurrent_cleanup_callbacks_await_one_uncancelled_task():
    """Concurrent callers both wait for the single ordered cleanup task."""
    order = []
    monitor_started = asyncio.Event()
    release_monitor = asyncio.Event()

    async def stop_monitoring():
        order.append("monitor")
        monitor_started.set()
        await release_monitor.wait()

    app = _make_app(order)
    app.stop_monitoring = stop_monitoring
    service = AppLifecycleService()

    first = asyncio.create_task(service.cleanup(app=app))
    await monitor_started.wait()
    second = asyncio.create_task(service.cleanup(app=app))
    await asyncio.sleep(0)

    assert not first.done()
    assert not second.done()
    assert order == ["monitor"]

    release_monitor.set()
    await asyncio.gather(first, second)

    assert order == ["monitor", "telemetry", "api", "destroy"]
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")
    app._api_client.close.assert_awaited_once_with()
    app.page.window.destroy.assert_awaited_once_with()


class _MemoryConnection(Connection):
    """Only replace Flet's transport, leaving session/event dispatch intact."""

    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_running_loop()
        self.pubsubhub = PubSubHub(loop=self.loop)
        self.messages = []
        self.disposed = False

    def send_message(self, message):
        self.messages.append(message)

    def dispose(self):
        self.disposed = True


def _make_flet_app(monkeypatch):
    connection = _MemoryConnection()
    session = Session(connection)
    app = SimLapsApp.__new__(SimLapsApp)
    app.page = session.page
    app._config = AppConfig()
    app._app_lifecycle_service = AppLifecycleService()
    app._setup_page()
    session.get_page_patch()
    order = []
    started, release, destroyed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def stop_monitoring():
        order.append("monitor")
        started.set()
        await release.wait()

    async def destroy():
        order.append("destroy")
        destroyed.set()

    app.stop_monitoring = stop_monitoring
    app._stop_telemetry_capture = AsyncMock(side_effect=lambda **kwargs: order.append("telemetry"))
    app._api_client = SimpleNamespace(close=AsyncMock(side_effect=lambda: order.append("api")))
    # Native destruction is the terminal transport boundary under test.
    monkeypatch.setattr(app.page.window, "destroy", AsyncMock(side_effect=destroy))
    return app, session, connection, order, started, release, destroyed


@pytest.mark.asyncio
async def test_flet_native_close_dispatch_awaits_ordered_cleanup(monkeypatch):
    app, session, _, order, started, release, _ = _make_flet_app(monkeypatch)
    assert app.page.window.prevent_close is True
    await session.dispatch_event(app.page.window._i, "event", {"type": "focus"})
    assert order == []
    dispatch = asyncio.create_task(session.dispatch_event(app.page.window._i, "event", {"type": "close"}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not dispatch.done()
        assert order == ["monitor"]
        app.page.window.destroy.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(dispatch, 2)
    assert order == ["monitor", "telemetry", "api", "destroy"]
    app._stop_telemetry_capture.assert_awaited_once_with(reason="app_close")


@pytest.mark.asyncio
async def test_flet_disconnect_awaits_cleanup_after_native_dispatch_cancellation(monkeypatch):
    app, session, connection, order, started, release, _ = _make_flet_app(monkeypatch)
    native = asyncio.create_task(session.dispatch_event(app.page.window._i, "event", {"type": "close"}))
    fallback = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        native.cancel()
        with pytest.raises(asyncio.CancelledError):
            await native
        fallback = asyncio.create_task(session.disconnect(session_timeout_seconds=60))
        await asyncio.sleep(0)
        assert connection.disposed
        assert not fallback.done()
        assert order == ["monitor"]
    finally:
        release.set()
        await asyncio.wait_for(app._app_lifecycle_service._cleanup_task, 2)
        if fallback is not None:
            await asyncio.wait_for(fallback, 2)
    assert order == ["monitor", "telemetry", "api", "destroy"]
    app.page.window.destroy.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_flet_session_close_schedules_best_effort_cleanup(monkeypatch):
    app, session, _, order, started, release, destroyed = _make_flet_app(monkeypatch)
    # Session expiration returns synchronously. Its scheduled handler can
    # finish only while the event loop remains alive.
    assert session.close() is None
    assert order == []
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not destroyed.is_set()
        assert order == ["monitor"]
    finally:
        release.set()
        await asyncio.wait_for(destroyed.wait(), 2)
        await asyncio.wait_for(app._app_lifecycle_service._cleanup_task, 2)
        await asyncio.sleep(0)
    assert order == ["monitor", "telemetry", "api", "destroy"]
