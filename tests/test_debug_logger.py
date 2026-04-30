"""Focused tests for src.utils.debug_logger."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils import debug_logger


def test_get_debug_log_path_script_mode_uses_repo_root() -> None:
    with patch.object(debug_logger.sys, "frozen", False, create=True):
        path = debug_logger._get_debug_log_path()
    assert isinstance(path, Path)
    assert path.name == "simlaps_debug.log"


def test_get_debug_log_path_frozen_mode_uses_executable_parent() -> None:
    with patch.object(debug_logger.sys, "frozen", True, create=True), patch.object(
        debug_logger.sys,
        "executable",
        r"C:\\app\\simlaps.exe",
        create=True,
    ):
        path = debug_logger._get_debug_log_path()
    assert path == Path(r"C:\app") / "simlaps_debug.log"


def test_log_writes_when_started_and_ignores_when_not_started() -> None:
    debug_logger.DebugLogger._file = MagicMock()
    debug_logger.DebugLogger._started = True

    logger = debug_logger.DebugLogger()
    logger.log("hello")

    assert debug_logger.DebugLogger._file.write.called

    debug_logger.DebugLogger._file.reset_mock()
    debug_logger.DebugLogger._started = False
    logger.log("ignored")
    debug_logger.DebugLogger._file.write.assert_not_called()


def test_close_resets_state_even_if_file_close_raises() -> None:
    file_mock = MagicMock()
    file_mock.close.side_effect = RuntimeError("boom")
    debug_logger.DebugLogger._file = file_mock
    debug_logger.DebugLogger._started = True

    logger = debug_logger.DebugLogger()
    logger.close()

    assert debug_logger.DebugLogger._file is None
    assert debug_logger.DebugLogger._started is False
