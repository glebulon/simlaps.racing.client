"""Focused tests for src.utils.helpers."""

from src.utils.helpers import (
    format_fuel,
    format_lap_time,
    format_sector_time,
    format_track_name,
    truncate_string,
)


def test_format_lap_time_handles_invalid_and_minute_values() -> None:
    assert format_lap_time(0) == "--:--.---"
    assert format_lap_time(-1) == "--:--.---"
    assert format_lap_time(58123) == "58.123"
    assert format_lap_time(125678) == "2:05.678"


def test_format_sector_time_handles_none_and_positive_values() -> None:
    assert format_sector_time(None) == "-"
    assert format_sector_time(0) == "-"
    assert format_sector_time(-10) == "-"
    assert format_sector_time(45123) == "45.123"


def test_format_fuel_formats_two_decimals() -> None:
    assert format_fuel(None) == "-"
    assert format_fuel(2.0) == "2.00 L"
    assert format_fuel(2.345) == "2.35 L"


def test_truncate_string_respects_max_length() -> None:
    assert truncate_string("short", max_length=10) == "short"
    assert truncate_string("1234567890", max_length=10) == "1234567890"
    assert truncate_string("abcdefghijklmnopqrstuvwxyz", max_length=10) == "abcdefg..."


def test_format_track_name_known_and_fallback() -> None:
    assert format_track_name("spa_francorchamps") == "Spa-Francorchamps"
    assert format_track_name("NURBURGRING_GP") == "Nürburgring"
    assert format_track_name("my_custom_track") == "My Custom Track"
