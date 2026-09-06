"""Coverage tests for the application entrypoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.main as main_mod
import src.ui.app as app_mod


def test_main_runs_app_without_creating_an_event_loop() -> None:
    with patch("src.main.run_app") as run_app, patch(
        "asyncio.new_event_loop", side_effect=AssertionError("unexpected loop")
    ) as new_event_loop:
        main_mod.main()

    run_app.assert_called_once()
    new_event_loop.assert_not_called()


@patch("src.main.run_app", side_effect=KeyboardInterrupt)
@patch("src.main.sys.exit")
def test_main_keyboard_interrupt_exits_zero(mock_exit, _mock_run_app) -> None:
    with patch("src.main.log_info") as log_info:
        main_mod.main()
    log_info.assert_called_once_with(main_mod.Component.APP, "Shutting down")
    mock_exit.assert_called_once_with(0)


@patch("src.main.run_app", side_effect=RuntimeError("fatal"))
@patch("src.main.sys.exit")
def test_main_exception_exits_one(mock_exit, _mock_run_app) -> None:
    with patch("src.main.log_exception") as log_exception:
        with patch.object(main_mod.sys, "frozen", False, create=True):
            main_mod.main()
    logged_exception = log_exception.call_args.args[2]
    assert isinstance(logged_exception, RuntimeError)
    log_exception.assert_called_once_with(
        main_mod.Component.APP,
        "Fatal error",
        logged_exception,
    )
    mock_exit.assert_called_once_with(1)


@patch("src.main.run_app", side_effect=RuntimeError("fatal"))
@patch("src.main.sys.exit")
@patch("builtins.input")
def test_main_exception_frozen_prompts_user(
    mock_input, mock_exit, _mock_run_app
) -> None:
    with patch.object(main_mod.sys, "frozen", True, create=True):
        with patch.object(main_mod.sys, "_MEIPASS", "C:\\fake", create=True):
            main_mod.main()
    mock_exit.assert_called_once_with(1)
    mock_input.assert_called_once()


def test_frozen_path_setup() -> None:
    with patch.object(main_mod.sys, "frozen", True, create=True):
        with patch.object(main_mod.sys, "_MEIPASS", "C:\\fake", create=True):
            with patch.object(main_mod.sys, "path", []):
                # Re-import to trigger path setup
                import importlib

                importlib.reload(main_mod)
                assert "C:\\fake" in main_mod.sys.path


def _configure_mock_app(app_class):
    app = app_class.return_value
    app._config = MagicMock(
        server_url="https://example.test",
        discord_enabled=False,
        discord_webhook_url=None,
        discord_pb_only=False,
    )
    app._pb_cache.is_loaded.return_value = False
    app._bootstrap_startup_user = AsyncMock()
    app.start_monitoring = AsyncMock()


@pytest.mark.asyncio
async def test_flet_entrypoint_installs_handler_on_running_loop() -> None:
    loop = MagicMock()
    page = MagicMock()

    with patch("src.ui.app.asyncio.get_running_loop", return_value=loop), patch(
        "src.ui.components.debug_logs.start_log_capture"
    ), patch.object(app_mod, "SimLapsApp") as app_class, patch(
        "src.ui.app.get_steam_user", return_value=(None, None)
    ):
        _configure_mock_app(app_class)
        await app_mod.main(page)

    loop.set_exception_handler.assert_called_once()

    handler = loop.set_exception_handler.call_args.args[0]
    error = RuntimeError("background failure")
    with patch("src.ui.app.log_exception") as log_exception:
        handler(loop, {"message": "task failed", "exception": error})

    log_exception.assert_called_once_with(app_mod.Component.APP, "task failed", error)


@pytest.mark.asyncio
async def test_background_exception_routes_to_structured_logger() -> None:
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    page = MagicMock()

    try:
        with patch("src.ui.components.debug_logs.start_log_capture"), patch.object(
            app_mod, "SimLapsApp"
        ) as app_class, patch(
            "src.ui.app.get_steam_user", return_value=(None, None)
        ), patch("src.ui.app.log_exception") as log_exception:
            _configure_mock_app(app_class)
            await app_mod.main(page)

            async def fail_in_background():
                raise RuntimeError("background failure")

            task = asyncio.create_task(fail_in_background())
            await asyncio.sleep(0)
            error = task.exception()
            loop.call_exception_handler({"message": "task failed", "exception": error})

            log_exception.assert_called_once_with(
                app_mod.Component.APP, "task failed", error
            )
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_async_exception_handler_without_exception() -> None:
    loop = MagicMock()
    page = MagicMock()

    with patch("src.ui.app.asyncio.get_running_loop", return_value=loop), patch(
        "src.ui.components.debug_logs.start_log_capture"
    ), patch.object(app_mod, "SimLapsApp") as app_class, patch(
        "src.ui.app.get_steam_user", return_value=(None, None)
    ):
        _configure_mock_app(app_class)
        await app_mod.main(page)

    handler = loop.set_exception_handler.call_args.args[0]
    with patch("src.ui.app.log_error") as log_error:
        handler(loop, {"message": "task failed"})

    log_error.assert_called_once_with(app_mod.Component.APP, "task failed")
