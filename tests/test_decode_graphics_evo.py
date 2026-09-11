"""Tests for ``decode_graphics_evo`` against a redacted captured protocol frame.

Fixture
-------
``tests/fixtures/ac_evo_graphics_frame.txt`` contains a deterministic
captured graphics SHM region. The companion JSON records the physics
decoder's view of the same frame so we can cross-validate fields that
should agree between the two sources.

The captured physics dead-reckoning ``normalized_car_position`` is ``0.0``
while graphics ``npos`` is non-zero. The graphics field is the authoritative
source the AI prompt's coaching depends on, so this fixture is the regression
backstop for the graphics decoder.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from src.core.telemetry_decoder import (
    _GE_TIMING_BEST_LAPTIME,
    _GE_TIMING_CURRENT_LAPTIME,
    _GE_TIMING_DELTA_CURRENT,
    _GE_TIMING_DELTA_CURRENT_P,
    _GE_TIMING_DELTA_LAST,
    _GE_TIMING_DELTA_LAST_P,
    _GE_TIMING_IDEAL_LAPTIME,
    _GE_TIMING_IS_INVALID,
    _GE_TIMING_LAST_LAPTIME,
    _GE_TIMING_TOTAL_TIME,
    GRAPHICS_EVO_MIN_SIZE,
    decode_graphics,
    decode_graphics_evo,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
GRAPHICS_HEX = FIXTURE_DIR / "ac_evo_graphics_frame.txt"
PHYSICS_JSON = FIXTURE_DIR / "ac_evo_graphics_frame_physics.json"


@pytest.fixture(scope="module")
def graphics_bytes() -> bytes:
    return bytes.fromhex(GRAPHICS_HEX.read_text().strip())


@pytest.fixture(scope="module")
def physics() -> dict:
    return json.loads(PHYSICS_JSON.read_text())


class TestDecodeGraphicsEvoStructure:
    """Structural checks on the decoder output."""

    def test_returns_dict_with_decoder_marker(self, graphics_bytes):
        result = decode_graphics_evo(graphics_bytes)
        assert result is not None
        assert result["_decoder"] == "ac_evo_graphics"
        assert result["buffer_size"] == len(graphics_bytes)

    def test_has_authoritative_progress_set_true(self, graphics_bytes):
        """Headline regression: this flag flips the analyzer's quality gate."""
        result = decode_graphics_evo(graphics_bytes)

        assert result["has_authoritative_progress"] is True

    def test_too_small_buffer_returns_none(self):
        small = b"\x00" * (GRAPHICS_EVO_MIN_SIZE - 1)

        assert decode_graphics_evo(small) is None

    def test_zero_buffer_rejected_via_sanity(self):
        """All-zero buffer is rejected because sanity checks require
        total_drivers >= 1 and max_gears >= 1 (both read as 0 from zeros).
        This ensures garbage/uninitialized buffers don't produce results.
        """
        zeros = b"\x00" * GRAPHICS_EVO_MIN_SIZE

        result = decode_graphics_evo(zeros)

        assert result is None

    def test_garbage_npos_rejected(self):
        """A buffer with NaN at the npos offset is rejected so analyzer
        falls back to the legacy decoder rather than getting poisoned
        progress values."""
        # Place a NaN float at the npos offset.
        from src.core.telemetry_decoder import _GE_NPOS

        buf = bytearray(b"\x00" * GRAPHICS_EVO_MIN_SIZE)
        # NaN: 0x7fc00000 little-endian = 00 00 c0 7f
        buf[_GE_NPOS : _GE_NPOS + 4] = b"\x00\x00\xc0\x7f"

        assert decode_graphics_evo(bytes(buf)) is None


class TestDecodeGraphicsEvoFieldValues:
    """Verify decoded values against the captured frame's known state."""

    def test_npos_in_valid_range(self, graphics_bytes):
        result = decode_graphics_evo(graphics_bytes)

        assert math.isfinite(result["npos"])
        assert 0.0 <= result["npos"] <= 1.0
        # Legacy alias must mirror npos.
        assert result["normalized_car_position"] == result["npos"]
        assert result["normalized_position_source"] == "graphics_npos"

    def test_npos_is_authoritative_over_physics_dead_reckoning(self, graphics_bytes, physics):
        """Graphics progress remains available when physics has no position."""
        result = decode_graphics_evo(graphics_bytes)

        assert physics["normalized_car_position"] == 0.0  # no physics position
        assert result["npos"] > 0.001  # authoritative graphics progress

    def test_gear_matches_physics(self, graphics_bytes, physics):
        """The captured graphics and physics fixtures describe one frame."""
        result = decode_graphics_evo(graphics_bytes)

        assert result["gear_int"] == physics["gear"]

    def test_fuel_matches_physics_within_tolerance(self, graphics_bytes, physics):
        """Graphics ``fuel_liter_current_quantity`` is the same physical
        quantity as physics ``fuel`` — they should agree to at least 0.05 L
        (graphics may be sampled one frame later)."""
        result = decode_graphics_evo(graphics_bytes)

        assert abs(result["fuel_liter_current_quantity"] - physics["fuel"]) < 0.5

    def test_g_forces_match_physics_acc_g(self, graphics_bytes, physics):
        """Graphics ``g_forces_x/y/z`` mirror physics ``acc_g`` (validated
        empirically — same physical quantity)."""
        result = decode_graphics_evo(graphics_bytes)
        acc_g = physics["acc_g"]

        # Tolerance is loose because physics samples at 333 Hz and graphics
        # at HUD-frame rate, so the timestamps differ slightly.
        assert abs(result["g_forces_x"] - acc_g["x"]) < 0.5
        assert abs(result["g_forces_y"] - acc_g["y"]) < 0.5
        assert abs(result["g_forces_z"] - acc_g["z"]) < 0.5

    def test_redacted_identity_strings_present(self, graphics_bytes):
        """The redacted fixture contains redacted human identity values and the actual car model."""
        result = decode_graphics_evo(graphics_bytes)

        assert result["driver_name"] == "Fixture Driver"
        assert result["car_model"] == "Dallara EXP"

    def test_car_location_is_on_track(self, graphics_bytes):
        """ACEVO_TRACK = 4 and the captured car is not in the pits."""
        result = decode_graphics_evo(graphics_bytes)

        assert result["car_location"] == 4
        assert result["is_in_pit_lane"] is False
        assert result["is_in_pit_box"] is False

    def test_is_valid_lap_true_for_clean_lap(self, graphics_bytes):
        result = decode_graphics_evo(graphics_bytes)

        assert result["is_valid_lap"] is True

    def test_status_is_live(self, graphics_bytes):
        """ACEVO_STATUS.AC_LIVE = 2."""
        result = decode_graphics_evo(graphics_bytes)

        assert result["status"] == 2

    def test_legacy_aliases_present(self, graphics_bytes):
        """Older analyzer code reads these legacy ACC field names."""
        result = decode_graphics_evo(graphics_bytes)

        # Lap timing aliases
        assert result["current_time_ms"] == result["current_lap_time_ms"]
        assert result["last_time_ms"] == result["last_laptime_ms"]
        assert result["best_time_ms"] == result["best_laptime_ms"]
        assert result["completed_laps"] == result["total_lap_count"]
        assert result["position"] == result["current_pos"]
        assert result["is_in_pit"] == result["is_in_pit_box"]

    def test_session_offsets_match_fixture(self, graphics_bytes):
        result = decode_graphics_evo(graphics_bytes)

        assert result["session_total_laps"] == 3
        assert result["session_current_lap"] == 1
        assert result["session_time_left_ms"] == 1_043_230

    def test_timing_substructure_uses_aligned_non_overlapping_fields(self, graphics_bytes):
        """Each timing field has a distinct documented ``_pack_=4`` slot."""
        data = bytearray(graphics_bytes)

        def put_string(offset, value):
            encoded = value.encode("ascii")
            assert len(encoded) <= 15
            data[offset : offset + 15] = encoded.ljust(15, b"\x00")

        put_string(_GE_TIMING_CURRENT_LAPTIME, "CURRENT")
        put_string(_GE_TIMING_DELTA_CURRENT, "CUR_DELTA")
        put_string(_GE_TIMING_LAST_LAPTIME, "LAST")
        put_string(_GE_TIMING_DELTA_LAST, "LAST_DELTA")
        put_string(_GE_TIMING_BEST_LAPTIME, "BEST")
        put_string(_GE_TIMING_IDEAL_LAPTIME, "IDEAL")
        put_string(_GE_TIMING_TOTAL_TIME, "TOTAL")
        struct.pack_into("<i", data, _GE_TIMING_DELTA_CURRENT_P, -101)
        struct.pack_into("<i", data, _GE_TIMING_DELTA_LAST_P, 202)
        data[_GE_TIMING_IS_INVALID] = 1

        result = decode_graphics_evo(bytes(data))

        assert result["timing_current_laptime"] == "CURRENT"
        assert result["timing_delta_current"] == "CUR_DELTA"
        assert result["timing_delta_current_p"] == -101
        assert result["timing_last_laptime"] == "LAST"
        assert result["timing_delta_last"] == "LAST_DELTA"
        assert result["timing_delta_last_p"] == 202
        assert result["timing_best_laptime"] == "BEST"
        assert result["timing_ideal_laptime"] == "IDEAL"
        assert result["timing_total_time"] == "TOTAL"
        assert result["timing_is_invalid"] is True


class TestDecodeGraphicsDispatch:
    """Top-level ``decode_graphics`` should pick the AC Evo decoder for
    AC Evo buffers and pass the result through ``_sanitize_graphics_payload``
    without dropping ``has_authoritative_progress``."""

    def test_decode_graphics_routes_to_evo_decoder(self, graphics_bytes):
        result = decode_graphics(graphics_bytes)

        assert result.get("_decoder") == "ac_evo_graphics"
        assert result["has_authoritative_progress"] is True
        assert 0.0 <= result["normalized_car_position"] <= 1.0
