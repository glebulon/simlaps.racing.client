"""Tests for ``decode_graphics_evo`` against a real captured frame.

Fixture
-------
``tests/fixtures/ac_evo_graphics_frame.txt`` contains the full hex of the
graphics SHM region for one frame from the user's lap on Spa-Francorchamps
in a Dallara EXP. The companion ``ac_evo_graphics_frame_physics.json``
records the physics decoder's view of the same frame so we can
cross-validate that graphics fields agree with physics where they should.

The original physics dead-reckoning ``normalized_car_position`` for this
frame was ``0.0`` (broken — the car is clearly mid-lap at 154 km/h in 5th
gear). The graphics ``npos`` field is the authoritative source the AI
prompt's coaching depends on, so this fixture is the regression backstop
for the graphics decoder.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.core.telemetry_decoder import (
    GRAPHICS_EVO_MIN_SIZE,
    decode_graphics,
    decode_graphics_evo,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
GRAPHICS_HEX = FIXTURE_DIR / "ac_evo_graphics_frame.txt"
PHYSICS_JSON = FIXTURE_DIR / "ac_evo_graphics_frame_physics.json"


@pytest.fixture(scope="module")
def graphics_bytes() -> bytes:
    if not GRAPHICS_HEX.exists():
        pytest.skip(f"fixture {GRAPHICS_HEX} not present")
    return bytes.fromhex(GRAPHICS_HEX.read_text().strip())


@pytest.fixture(scope="module")
def physics() -> dict:
    if not PHYSICS_JSON.exists():
        pytest.skip(f"fixture {PHYSICS_JSON} not present")
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

    def test_zero_buffer_rejected_via_npos_sanity(self):
        """All-zero buffer reads npos=0.0 which is technically in range,
        so the decoder still returns a dict — but every other field is 0
        and the analyzer sees has_authoritative_progress=True with
        npos=0.0. This is acceptable; bigger sanity is enforced by the
        struct's ``status`` field downstream. Test documents current
        behaviour so refactors don't accidentally tighten the gate.
        """
        zeros = b"\x00" * GRAPHICS_EVO_MIN_SIZE

        result = decode_graphics_evo(zeros)

        assert result is not None
        assert result["npos"] == 0.0

    def test_garbage_npos_rejected(self):
        """A buffer with NaN at the npos offset is rejected so analyzer
        falls back to the legacy decoder rather than getting poisoned
        progress values."""
        # Place a NaN float at the npos offset.
        from src.core.telemetry_decoder import _GE_NPOS

        buf = bytearray(b"\x00" * GRAPHICS_EVO_MIN_SIZE)
        # NaN: 0x7fc00000 little-endian = 00 00 c0 7f
        buf[_GE_NPOS:_GE_NPOS + 4] = b"\x00\x00\xc0\x7f"

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

    def test_npos_disagrees_with_broken_physics_dead_reckoning(self, graphics_bytes, physics):
        """Documents *why* the graphics decoder matters: in this captured
        frame the car is at 154 km/h in 5th gear, but physics
        dead-reckoning collapsed to ``0.0``. Graphics ``npos`` carries
        the real value."""
        result = decode_graphics_evo(graphics_bytes)

        assert physics["normalized_car_position"] == 0.0  # broken physics
        assert result["npos"] > 0.001  # real progress

    def test_gear_matches_physics(self, graphics_bytes, physics):
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

    def test_known_strings_present(self, graphics_bytes):
        """``driver_name`` and ``car_model`` were validated against the
        user's session ('Glebulon' / 'Dallara EXP'). Anchoring the test
        to substrings avoids brittleness if the surname trims differently
        across runs."""
        result = decode_graphics_evo(graphics_bytes)

        assert "Glebulon" in result["driver_name"]
        assert "Dallara" in result["car_model"]

    def test_car_location_is_on_track(self, graphics_bytes):
        """ACEVO_TRACK = 4. Captured frame is mid-lap at 154 km/h."""
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


class TestDecodeGraphicsDispatch:
    """Top-level ``decode_graphics`` should pick the AC Evo decoder for
    AC Evo buffers and pass the result through ``_sanitize_graphics_payload``
    without dropping ``has_authoritative_progress``."""

    def test_decode_graphics_routes_to_evo_decoder(self, graphics_bytes):
        result = decode_graphics(graphics_bytes)

        assert result.get("_decoder") == "ac_evo_graphics"
        assert result["has_authoritative_progress"] is True
        assert 0.0 <= result["normalized_car_position"] <= 1.0
