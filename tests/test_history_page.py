"""Regression tests for HistoryPage timestamp parsing behavior."""

from unittest.mock import patch

from src.ui.pages.history import HistoryEntry, HistoryPage
from src.utils.structured_logger import Component


def _extract_displayed_time_and_date(row):
    datetime_column = row.content.controls[0]
    return datetime_column.controls[0].value, datetime_column.controls[1].value


def _make_entry(timestamp):
    return HistoryEntry(
        track="laguna_seca",
        car="ks_porsche_992_gt3_cup",
        lap_time_ms=89556,
        timestamp=timestamp,
        was_submitted=False,
        was_valid=True,
    )


def test_build_entry_row_malformed_timestamp_uses_placeholders_without_unexpected_log() -> None:
    page = HistoryPage()
    entry = _make_entry("not-an-iso-timestamp")

    with patch("src.utils.structured_logger.log_exception") as log_exception_mock:
        row = page._build_entry_row(entry)

    time_str, date_str = _extract_displayed_time_and_date(row)
    assert time_str == "--:--"
    assert date_str == "---"
    log_exception_mock.assert_not_called()


def test_build_entry_row_unexpected_timestamp_error_is_logged() -> None:
    page = HistoryPage()
    entry = _make_entry("2026-05-10T12:00:00")

    with (
        patch("src.ui.pages.history.datetime") as datetime_mock,
        patch("src.utils.structured_logger.log_exception") as log_exception_mock,
    ):
        datetime_mock.fromisoformat.side_effect = RuntimeError("boom")
        row = page._build_entry_row(entry)

    time_str, date_str = _extract_displayed_time_and_date(row)
    assert time_str == "--:--"
    assert date_str == "---"

    log_exception_mock.assert_called_once()
    args, kwargs = log_exception_mock.call_args
    assert args[0] == Component.HISTORY
    assert args[1] == "Unexpected error parsing history timestamp"
    assert isinstance(args[2], RuntimeError)
    assert kwargs["timestamp"] == entry.timestamp
