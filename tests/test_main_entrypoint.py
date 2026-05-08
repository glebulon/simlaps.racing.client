"""Coverage tests for src.main entrypoint behavior."""

from unittest.mock import MagicMock, patch

import pytest

import src.main as main_mod


@patch("src.main.run_app")
@patch("src.main.asyncio.set_event_loop")
@patch("src.main.asyncio.new_event_loop")
def test_main_runs_app_successfully(
    mock_new_loop,
    mock_set_loop,
    mock_run_app,
) -> None:
    loop = MagicMock()
    mock_new_loop.return_value = loop

    main_mod.main()

    mock_new_loop.assert_called_once()
    mock_set_loop.assert_called_once_with(loop)
    loop.set_exception_handler.assert_called_once()
    mock_run_app.assert_called_once()


@patch("src.main.run_app", side_effect=KeyboardInterrupt)
@patch("src.main.sys.exit")
def test_main_keyboard_interrupt_exits_zero(mock_exit, _mock_run_app) -> None:
    main_mod.main()
    mock_exit.assert_called_once_with(0)


@patch("src.main.run_app", side_effect=RuntimeError("fatal"))
@patch("src.main.sys.exit")
def test_main_exception_exits_one(mock_exit, _mock_run_app) -> None:
    with patch.object(main_mod.sys, "frozen", False, create=True):
        main_mod.main()
    mock_exit.assert_called_once_with(1)
